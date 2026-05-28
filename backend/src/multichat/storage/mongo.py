"""基于 motor 的 MongoStorage 实现

实现要点:
    - AsyncIOMotorClient 与事件循环强绑定 必须在使用它的 loop 中创建
      所以不在模块顶层建客户端 而是在 fastapi lifespan startup 调 connect 阶段建
    - 所有写操作具备幂等性 通过业务唯一键 upsert 或 update_one
    - 业务层只接触 string 形式的 session_id / task_id 用 uuid4().hex 生成
    - ObjectId 不暴露 mongo _id 字段不被业务读到
    - 写失败直接抛 调用方决定降级 流式 chunk 容忍方案留到 M2 reply 阶段再做

提供两种构造路径:
    - MotorMongoStorage(uri, db) 真实场景 内部用 motor.motor_asyncio.AsyncIOMotorClient
    - MotorMongoStorage.from_client(client, db) 测试场景 注入 mongomock-motor
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import structlog
from pymongo import ASCENDING, DESCENDING

from ..core.models import (
    AgentRecord,
    AuthSessionRecord,
    McpServerConfig,
    ModelCatalogEntry,
    Round,
    Session,
    SessionMeta,
    SkillConfig,
    TaskState,
    UserRecord,
)

_logger = structlog.get_logger(__name__)

# settings 集合中存放 judge 指针的固定文档 _id 与字段名
_JUDGE_POINTER_DOC_ID = "judge_pointer"
_JUDGE_POINTER_FIELD = "target_agent_name"


def _utcnow() -> datetime:
    """统一使用带时区的 UTC 时间 落库取值都走这里"""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """业务对外暴露的字符串 id 用 uuid4 hex 表示"""
    return uuid.uuid4().hex


def _new_agent_name() -> str:
    """生成内部稳定 agent name 形如 agent_<8 位 hex>

    碰撞概率约 1/4 亿 由 create_agent 调用方在 collision 时直接抛 ValueError 让前端重试
    """
    return f"agent_{uuid.uuid4().hex[:8]}"


def _strip_internal(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """剥离 mongo 内部 _id 字段 业务层不消费"""
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


def _normalize_models(models: list | None) -> list[dict[str, Any]] | None:
    """把 ModelCatalogEntry / dict 混合列表统一转成 dict 列表

    None 直接透传 表示"不更新此字段"
    max_input_tokens 必填 dict 形式传入时缺失或 <=0 抛 ValueError
        让路由层 422 而不是落库后 reload 时才炸
    """
    if models is None:
        return None
    out: list[dict[str, Any]] = []
    for m in models:
        if isinstance(m, ModelCatalogEntry):
            out.append(m.model_dump(mode="json"))
        elif isinstance(m, dict):
            tokens_raw = m.get("max_input_tokens")
            try:
                tokens = int(tokens_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"available_models[{m.get('model_id')}] 缺 max_input_tokens 或非整数"
                ) from exc
            if tokens <= 0:
                raise ValueError(
                    f"available_models[{m.get('model_id')}] max_input_tokens 必须 >0"
                )
            out.append(
                {
                    "model_id": str(m.get("model_id", "")),
                    "label": str(m.get("label", "")),
                    "max_input_tokens": tokens,
                }
            )
        else:
            raise TypeError(f"available_models 项类型不支持 {type(m).__name__}")
    return out


def _agent_doc_to_record(doc: dict[str, Any]) -> AgentRecord:
    """把数据库文档转成 AgentRecord  补默认值兜底老数据

    available_models 兼容老数据:
        历史文档没有 max_input_tokens 字段 直接验证会 422
        读取阶段补一个保守默认 200000 让 agent 能加载起来
        前端在配置页能看到默认值 提醒用户改成实际值
        只兜底读取 不主动回写 避免错值固化到 DB
    """
    raw_models = doc.get("available_models", []) or []
    legacy_default_tokens = 200000
    fixed_models: list[dict[str, Any]] = []
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        item = {
            "model_id": str(m.get("model_id", "")),
            "label": str(m.get("label", "")),
        }
        tokens_raw = m.get("max_input_tokens")
        try:
            tokens = int(tokens_raw) if tokens_raw is not None else legacy_default_tokens
        except (TypeError, ValueError):
            tokens = legacy_default_tokens
        if tokens <= 0:
            tokens = legacy_default_tokens
        item["max_input_tokens"] = tokens
        fixed_models.append(item)

    return AgentRecord.model_validate(
        {
            "name": doc["name"],
            "owner_user_id": doc.get("owner_user_id", "system"),
            "display_name": doc.get("display_name") or doc["name"],
            "provider_type": doc.get("provider_type", "openai_compatible"),
            "base_url": doc.get("base_url", ""),
            "api_key": doc.get("api_key", ""),
            "model": doc.get("model", ""),
            "available_models": fixed_models,
            "prompt": doc.get("prompt", ""),
            "version": int(doc.get("version", 1)),
            "updated_at": doc.get("updated_at", _utcnow()),
            "avatar": doc.get("avatar"),
            # 老数据可能没这个字段 兜底 None
            "avatar_data_url": doc.get("avatar_data_url"),
        }
    )


class MotorMongoStorage:
    """基于 motor 的 MongoStorage 实现

    线程/loop 注意事项:
        客户端实例与创建时的事件循环强绑定 不可跨 loop 复用
        本类实例仅供单一 fastapi 应用进程使用 不要在多线程里共享
    """

    def __init__(self, uri: str, database: str) -> None:
        self.uri = uri
        self.database = database
        self._client: Any = None
        self._db: Any = None
        # 标记是否由外部注入 client 测试场景下不应在 close 阶段销毁
        self._client_owned = True

    # ------------------------------------------------------------------ 工厂入口
    @classmethod
    async def connect(cls, settings: Any) -> "MotorMongoStorage":
        """fastapi lifespan 调用入口 建立 motor 客户端并保证索引就绪

        参数 settings 必须含 uri 和 db 字段 直接拿 MongoConfig 即可
        """
        from motor.motor_asyncio import AsyncIOMotorClient  # 延迟导入避免顶层副作用

        storage = cls(uri=settings.uri, database=settings.db)
        storage._client = AsyncIOMotorClient(settings.uri)
        storage._db = storage._client[settings.db]
        storage._client_owned = True
        await storage.ensure_indexes()
        _logger.info("mongo 客户端建立", uri=settings.uri, db=settings.db)
        return storage

    @classmethod
    def from_client(cls, client: Any, database: str) -> "MotorMongoStorage":
        """注入式工厂 单元测试用 mongomock-motor 时走这条路"""
        storage = cls(uri="<injected>", database=database)
        storage._client = client
        storage._db = client[database]
        storage._client_owned = False
        return storage

    async def close(self) -> None:
        """释放 motor 客户端 注入式 client 不在这里关 由调用方自行处理"""
        if self._client is None:
            return
        if self._client_owned:
            self._client.close()
        self._client = None
        self._db = None

    # ------------------------------------------------------------------ 索引
    async def ensure_indexes(self) -> None:
        """启动时确保关键索引就绪 多次调用幂等"""
        if self._db is None:
            raise RuntimeError("MotorMongoStorage 尚未 connect 就调用 ensure_indexes")

        # sessions 集合 session_id 唯一 updated_at 降序便于最近会话列表
        await self._db["sessions"].create_index(
            [("session_id", ASCENDING)], unique=True, name="uniq_session_id"
        )
        await self._db["sessions"].create_index(
            [("updated_at", DESCENDING)], name="idx_session_updated_at_desc"
        )

        # rounds 集合 task_id 唯一 session_id + round_index 复合索引
        await self._db["rounds"].create_index(
            [("task_id", ASCENDING)], unique=True, name="uniq_task_id"
        )
        await self._db["rounds"].create_index(
            [("session_id", ASCENDING), ("round_index", ASCENDING)],
            name="idx_round_session_index",
        )

        # agents 集合 name 唯一
        await self._db["agents"].create_index(
            [("name", ASCENDING)], unique=True, name="uniq_agent_name"
        )

        # users / auth_sessions
        await self._db["users"].create_index(
            [("username", ASCENDING)],
            unique=True,
            name="uniq_username",
        )
        await self._db["auth_sessions"].create_index(
            [("session_id", ASCENDING)], unique=True, name="uniq_auth_session_id"
        )

        # agent_history 集合 按 (name, version 降序) 查最近版本快
        # version 不强制唯一 因为同一名字不同时间会有多版 但同名同 version 唯一
        await self._db["agent_history"].create_index(
            [("name", ASCENDING), ("version", DESCENDING)],
            name="idx_agent_history_name_version_desc",
        )

        # mcp_servers 集合 索引迁移: 从旧的单字段 unique 改为复合 unique
        try:
            await self._db["mcp_servers"].drop_index("uniq_mcp_server_name")
        except Exception:
            # 索引不存在时忽略 (新部署或已迁移)
            pass
        await self._db["mcp_servers"].create_index(
            [("name", ASCENDING), ("owner_user_id", ASCENDING)],
            unique=True, name="uniq_mcp_server_name_owner",
        )

        # skills 集合 (name, owner_user_id) 复合唯一
        await self._db["skills"].create_index(
            [("name", ASCENDING), ("owner_user_id", ASCENDING)],
            unique=True, name="uniq_skill_name_owner",
        )

        # 数据迁移: settings 集合旧全局配置 → 新 per-user 集合
        await self._migrate_global_mcp_and_skills()

    # ================================================================ Auth
    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        role: Literal["owner", "member"] = "owner",
    ) -> UserRecord:
        """创建用户 username 全局唯一"""
        now = _utcnow()
        user_id = _new_id()
        doc = {
            "user_id": user_id,
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "created_at": now,
        }
        try:
            await self._db["users"].insert_one(doc)
        except Exception as exc:
            raise ValueError(
                f"username 已存在 username={username}"
            ) from exc
        return UserRecord.model_validate(doc)

    async def get_user_by_username(
        self,
        username: str,
    ) -> UserRecord | None:
        """按用户名全局查询用户"""
        doc = await self._db["users"].find_one({"username": username}, {"_id": 0})
        if doc is None:
            return None
        return UserRecord.model_validate(doc)

    async def get_user_by_id(self, user_id: str) -> UserRecord | None:
        """按 user_id 查询用户"""
        doc = await self._db["users"].find_one({"user_id": user_id}, {"_id": 0})
        if doc is None:
            return None
        return UserRecord.model_validate(doc)

    async def create_auth_session(
        self,
        user_id: str,
        *,
        expires_in_hours: int,
    ) -> AuthSessionRecord:
        """创建登录态 session"""
        now = _utcnow()
        session_id = _new_id()
        expires_at = now + timedelta(hours=expires_in_hours)
        doc = {
            "session_id": session_id,
            "user_id": user_id,
            "expires_at": expires_at,
            "created_at": now,
        }
        await self._db["auth_sessions"].insert_one(doc)
        return AuthSessionRecord.model_validate(doc)

    async def get_auth_session(self, session_id: str) -> AuthSessionRecord | None:
        """按 session_id 读取登录态"""
        doc = await self._db["auth_sessions"].find_one({"session_id": session_id}, {"_id": 0})
        if doc is None:
            return None
        return AuthSessionRecord.model_validate(doc)

    async def delete_auth_session(self, session_id: str) -> None:
        """删除登录态 session 不存在时静默忽略"""
        await self._db["auth_sessions"].delete_one({"session_id": session_id})

    # ============================================================ Sessions CRUD
    async def create_session(
        self,
        title: str | None = None,
        *,
        owner_user_id: str,
        parent_session_id: str | None = None,
        branch_from_task_id: str | None = None,
        branch_from_role: Literal["user", "assistant"] | None = None,
        branch_from_agent: str | None = None,
        draft_message: str | None = None,
    ) -> str:
        """新建会话 返回 string session_id"""
        session_id = _new_id()
        now = _utcnow()
        doc = {
            "session_id": session_id,
            "owner_user_id": owner_user_id,
            "title": title or "",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
            "parent_session_id": parent_session_id,
            "branch_from_task_id": branch_from_task_id,
            "branch_from_role": branch_from_role,
            "branch_from_agent": branch_from_agent,
            "draft_message": draft_message,
        }
        await self._db["sessions"].insert_one(doc)
        return session_id

    async def list_sessions(
        self,
        *,
        owner_user_id: str,
        limit: int = 50,
    ) -> list[SessionMeta]:
        """按 updated_at 倒序拉最近会话列表 不含 rounds 详情"""
        cursor = (
            self._db["sessions"]
            .find(
                {"owner_user_id": owner_user_id},
                {"_id": 0},
            )
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        out: list[SessionMeta] = []
        async for doc in cursor:
            out.append(SessionMeta.model_validate(doc))
        return out

    async def get_session(
        self,
        session_id: str,
        *,
        owner_user_id: str,
    ) -> Session | None:
        """单个会话详情 不内联 rounds 路由层视情况再 list_rounds"""
        doc = await self._db["sessions"].find_one(
            {
                "session_id": session_id,
                "owner_user_id": owner_user_id,
            }
        )
        if doc is None:
            return None
        _strip_internal(doc)
        return Session.model_validate(doc)

    async def update_session_meta(
        self, session_id: str, *, title: str | None = None
    ) -> None:
        """局部更新会话元信息 当前仅支持 title"""
        updates: dict[str, Any] = {"updated_at": _utcnow()}
        if title is not None:
            updates["title"] = title
        result = await self._db["sessions"].update_one(
            {"session_id": session_id}, {"$set": updates}
        )
        if result.matched_count == 0:
            raise KeyError(f"session 不存在 session_id={session_id}")

    async def update_session_summary(
        self,
        session_id: str,
        *,
        summary: str,
        summary_until_round: int,
    ) -> None:
        """覆盖更新会话摘要  单条 session 只保留一份摘要 不存历史

        参数:
            session_id: 会话 id  不存在抛 KeyError
            summary: 新摘要正文  允许空字符串(等价于"清空摘要")
            summary_until_round: 摘要覆盖到的 round_index
                后续 _build_history 拼回时只追加该 round_index 之后的轮次
                必须 >=0  否则抛 ValueError 防止误传 -1 之类把全部历史都当成"未摘要"

        约束:
            - 同 session 并发触发摘要靠 task_manager 端 asyncio.Lock 兜底
              这里不重入  $set 是原子操作 后写覆盖前写
            - summary_updated_at 一并刷新  方便前端展示"上次摘要时间"
            - 顶层 updated_at 也刷  保证会话列表能反映最近活动
        """
        if summary_until_round < 0:
            raise ValueError(
                f"summary_until_round 不能为负 实际 {summary_until_round}"
            )
        now = _utcnow()
        result = await self._db["sessions"].update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "summary": summary,
                    "summary_until_round": int(summary_until_round),
                    "summary_updated_at": now,
                    "updated_at": now,
                }
            },
        )
        if result.matched_count == 0:
            raise KeyError(f"session 不存在 session_id={session_id}")

    async def clear_session_summary(self, session_id: str) -> None:
        """清空 session 摘要与相关快照

        编辑历史消息后 如果摘要已经覆盖到被删除的后续轮次 这份摘要就不再可信
        这里一次性清掉 summary / summary_until_round / summary_updated_at / context_usage
        让后续上下文重新从剩余历史计算
        """
        result = await self._db["sessions"].update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "summary": "",
                    "summary_until_round": 0,
                    "summary_updated_at": None,
                    "context_usage": None,
                    "updated_at": _utcnow(),
                }
            },
        )
        if result.matched_count == 0:
            raise KeyError(f"session 不存在 session_id={session_id}")

    async def update_session_context_usage(
        self,
        session_id: str,
        usage: dict[str, Any] | None,
    ) -> None:
        """覆盖更新会话上下文用量快照  用于刷新页面时恢复进度条状态

        参数:
            session_id: 会话 id  不存在静默忽略  避免给 task_manager 主流加 try
            usage: token_counter.usage_payload 输出  None 表示清空

        说明:
            - 这是展示数据  失败不该阻塞 reply 流  上层 task_manager 已 except 兜底
            - $set 原子操作  并发竞态由 mongo 自身保证  无需上层 lock
            - 不刷顶层 updated_at  避免每轮 reply 都让会话列表抖动 (会话排序按 updated_at)
        """
        await self._db["sessions"].update_one(
            {"session_id": session_id},
            {"$set": {"context_usage": usage}},
        )

    async def delete_session(
        self,
        session_id: str,
        *,
        owner_user_id: str,
    ) -> int:
        """删除 session 与其下所有 rounds 返回删除的 round 数

        约束:
            - session 不存在抛 KeyError 路由层映射 404
            - 若 session 下存在进行中的 round 抛 ValueError 路由层映射 409
              进行中状态包含 spec 新值 + 历史值 与 cancel_orphan_rounds 对齐
        """
        sess = await self._db["sessions"].find_one(
            {
                "session_id": session_id,
                "owner_user_id": owner_user_id,
            }
        )
        if sess is None:
            raise KeyError(f"session 不存在 session_id={session_id}")

        in_progress = [
            "pending",
            "thinking",
            "think_done",
            "decided",
            "replying",
            # 历史值兼容 与 cancel_orphan_rounds 保持一致
            "created",
            "waiting_decision",
        ]
        active_cnt = await self._db["rounds"].count_documents(
            {
                "session_id": session_id,
                "state": {"$in": in_progress},
            }
        )
        if active_cnt > 0:
            raise ValueError(
                f"session 仍有 {active_cnt} 个进行中的 round 无法删除"
            )

        # 先删 rounds 再删 session 顺序保证即便中途失败也不会留下"无 session 的孤儿 round"
        rounds_res = await self._db["rounds"].delete_many({"session_id": session_id})
        await self._db["sessions"].delete_one(
            {
                "session_id": session_id,
                "owner_user_id": owner_user_id,
            }
        )
        return int(rounds_res.deleted_count or 0)

    # =============================================================== Rounds CRUD
    async def create_round(
        self,
        session_id: str,
        user_message: str,
        user_mention: str | None,
        agents: list[str],
        input_mode: Literal["single", "multi"] = "single",
        thinking_enabled: bool = False,
    ) -> str:
        """创建一轮新提问 自动累计 round_index 返回 task_id

        agents 必须非空  长度 1~4  路由层校验
        replies dict 同步初始化为 {agent: {"state":"pending","content":"","segments":[]}}
        让前端拉到这个 round 时直接看到所有候选格子的占位卡

        thinking_enabled 跟随用户当次输入框大脑开关  落到 round 顶层字段
        task_manager 在 reply 阶段读取  决定是否给 ChatOpenAI 注入 extra_body.thinking
        """
        # 先确认 session 存在 否则后续操作会留下孤儿 round
        session_doc = await self._db["sessions"].find_one({"session_id": session_id})
        if session_doc is None:
            raise KeyError(f"session 不存在 session_id={session_id}")

        if not agents:
            # 这里兜底 实际路由层应当已经拦截
            raise ValueError("create_round 需要至少 1 个 agent")
        if input_mode not in ("single", "multi"):
            raise ValueError(f"input_mode 取值非法 {input_mode}")
        if input_mode == "single" and len(agents) != 1:
            raise ValueError(f"single 模式 agents 必须正好 1 个 实际 {len(agents)}")
        if input_mode == "multi" and not (2 <= len(agents) <= 4):
            raise ValueError(f"multi 模式 agents 必须 2~4 个 实际 {len(agents)}")

        # 计算下一个 round_index 简单查最大值 +1
        last = await self._db["rounds"].find_one(
            {"session_id": session_id},
            sort=[("round_index", DESCENDING)],
            projection={"round_index": 1, "_id": 0},
        )
        next_index = (last["round_index"] + 1) if last else 0

        # 初始化 replies 占位  让前端 SSE 还没开推前也能看到 N 个卡片骨架
        replies_init: dict[str, dict[str, Any]] = {
            name: {"state": "pending", "content": "", "segments": []}
            for name in agents
        }

        task_id = _new_id()
        now = _utcnow()
        doc = {
            "task_id": task_id,
            "session_id": session_id,
            "round_index": next_index,
            "question": user_message,
            "user_mention": user_mention,
            "thinking_enabled": bool(thinking_enabled),
            "agents": list(agents),
            "input_mode": input_mode,
            "replies": replies_init,
            "selected_reply_agent": None,
            "state": TaskState.PENDING.value,
            "created_at": now,
            "updated_at": now,
        }
        await self._db["rounds"].insert_one(doc)
        # 同步 session updated_at 让会话列表能反映最近活动
        await self._db["sessions"].update_one(
            {"session_id": session_id},
            {"$set": {"updated_at": now, "draft_message": None}},
        )
        return task_id

    async def get_round(self, task_id: str) -> Round | None:
        doc = await self._db["rounds"].find_one({"task_id": task_id})
        if doc is None:
            return None
        _strip_internal(doc)
        return Round.model_validate(doc)

    async def list_rounds(self, session_id: str) -> list[Round]:
        """列出某会话所有轮次 按 round_index 升序"""
        cursor = (
            self._db["rounds"]
            .find({"session_id": session_id}, {"_id": 0})
            .sort("round_index", ASCENDING)
        )
        out: list[Round] = []
        async for doc in cursor:
            out.append(Round.model_validate(doc))
        return out

    async def update_round_state(self, task_id: str, state: TaskState) -> None:
        """状态机推进 集中入口 顺便刷 updated_at"""
        result = await self._db["rounds"].update_one(
            {"task_id": task_id},
            {"$set": {"state": state.value, "updated_at": _utcnow()}},
        )
        if result.matched_count == 0:
            raise KeyError(f"round 不存在 task_id={task_id}")

    async def update_round_field(self, task_id: str, path: str, value: Any) -> None:
        """按 dot path 局部更新 round 字段 例如 replies.glm.state

        value 若是 BaseModel 自动 dump 成 dict 避免上层手动转换
        """
        from pydantic import BaseModel

        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        elif isinstance(value, list):
            value = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]

        result = await self._db["rounds"].update_one(
            {"task_id": task_id},
            {"$set": {path: value, "updated_at": _utcnow()}},
        )
        if result.matched_count == 0:
            raise KeyError(f"round 不存在 task_id={task_id}")

    async def delete_rounds_after(self, session_id: str, round_index: int) -> int:
        """删除指定 round_index 之后的所有轮次

        编辑历史消息时用:
            保留目标 round 及其之前内容
            删除所有更晚的 round
        """
        result = await self._db["rounds"].delete_many(
            {"session_id": session_id, "round_index": {"$gt": int(round_index)}}
        )
        if result.deleted_count:
            await self._db["sessions"].update_one(
                {"session_id": session_id},
                {"$set": {"updated_at": _utcnow()}},
            )
        return int(result.deleted_count or 0)

    async def clone_session_branch(
        self,
        *,
        source_session_id: str,
        owner_user_id: str,
        source_task_id: str,
        source_role: Literal["user", "assistant"],
        source_agent: str | None = None,
    ) -> tuple[str, str | None]:
        """复制一个会话前缀生成分支 session

        规则:
            - source_role="user": 复制 source_task_id 之前的全部历史
              并把该 user question 作为 draft_message 返回给前端预填输入框
            - source_role="assistant": 复制 source_task_id 及其之前的历史
              source_agent 必填 且该 reply 必须是 done
              新会话中该 round.selected_reply_agent 强制指向 source_agent
            - 原会话摘要只有在"完整覆盖范围都落在复制前缀里"时才安全继承
              否则新会话不带 summary  改走原始 rounds 重算上下文
            - context_usage 不复制  避免沿用旧会话快照造成误导
        """
        source_session = await self._db["sessions"].find_one(
            {
                "session_id": source_session_id,
                "owner_user_id": owner_user_id,
            }
        )
        if source_session is None:
            raise KeyError(f"session 不存在 session_id={source_session_id}")

        source_round = await self.get_round(source_task_id)
        if source_round is None:
            raise KeyError(f"round 不存在 task_id={source_task_id}")
        if source_round.session_id != source_session_id:
            raise ValueError("source_task_id 不属于当前 session")

        if source_role == "assistant":
            if not source_agent:
                raise ValueError("assistant 分支必须提供 source_agent")
            picked_reply = (source_round.replies or {}).get(source_agent) or {}
            if picked_reply.get("state") != "done":
                raise ValueError("只能基于已完成的 assistant 回复创建分支")
            if (
                len(source_round.agents or []) > 1
                and source_round.selected_reply_agent
                and source_agent != source_round.selected_reply_agent
            ):
                raise ValueError("多 agent 场景下只能基于当前已选中的回答创建分支")
            last_included_round_index = int(source_round.round_index)
            draft_message: str | None = None
        elif source_role == "user":
            last_included_round_index = int(source_round.round_index) - 1
            draft_message = source_round.question
        else:
            raise ValueError(f"source_role 取值非法 {source_role}")

        title_seed = (source_round.question or source_session.get("title") or "分支会话").strip()
        branch_title = f"分支 · {title_seed[:32]}".strip()
        new_session_id = await self.create_session(
            branch_title,
            owner_user_id=owner_user_id,
            parent_session_id=source_session_id,
            branch_from_task_id=source_task_id,
            branch_from_role=source_role,
            branch_from_agent=source_agent if source_role == "assistant" else None,
            draft_message=draft_message,
        )

        # 复制前缀 rounds  由于是从头到某个分支点的前缀  round_index 可原样保留
        # task_id 必须重生成 避免与父会话冲突
        source_rounds = await self.list_rounds(source_session_id)
        cloned_docs: list[dict[str, Any]] = []
        for round_obj in source_rounds:
            if int(round_obj.round_index) > last_included_round_index:
                break
            doc = round_obj.model_dump(mode="python")
            doc["task_id"] = _new_id()
            doc["session_id"] = new_session_id
            doc["state"] = getattr(round_obj.state, "value", str(round_obj.state))
            if (
                source_role == "assistant"
                and round_obj.task_id == source_task_id
                and source_agent
            ):
                doc["selected_reply_agent"] = source_agent
            cloned_docs.append(doc)

        if cloned_docs:
            await self._db["rounds"].insert_many(cloned_docs)

        # 摘要只有在完整覆盖都落入复制前缀时才安全继承
        summary_text = str(source_session.get("summary") or "")
        summary_until = int(source_session.get("summary_until_round") or 0)
        summary_safe = bool(summary_text) and summary_until <= last_included_round_index
        if (
            summary_safe
            and source_role == "assistant"
            and summary_until == int(source_round.round_index)
            and source_agent != source_round.selected_reply_agent
        ):
            summary_safe = False
        await self._db["sessions"].update_one(
            {"session_id": new_session_id},
            {
                "$set": {
                    "summary": summary_text if summary_safe else "",
                    "summary_until_round": summary_until if summary_safe else 0,
                    "summary_updated_at": source_session.get("summary_updated_at")
                    if summary_safe
                    else None,
                    "context_usage": None,
                    "updated_at": _utcnow(),
                }
            },
        )
        return new_session_id, draft_message

    async def append_reply_chunk_for_agent(
        self, task_id: str, agent_name: str, chunk: str
    ) -> None:
        """流式回复追加片段 用 aggregation pipeline update 原子拼接 replies.{agent}.content

        与历史 append_reply_chunk 对比:
            老接口写顶层 reply.content  新接口写 replies.<agent>.content
            支持多 agent 并发  各 agent 之间互不影响
        """
        path = f"replies.{agent_name}.content"
        result = await self._db["rounds"].update_one(
            {"task_id": task_id},
            [
                {
                    "$set": {
                        path: {
                            "$concat": [
                                {"$ifNull": [f"${path}", ""]},
                                chunk,
                            ]
                        },
                        "updated_at": _utcnow(),
                    }
                }
            ],
        )
        if result.matched_count == 0:
            raise KeyError(f"round 不存在 task_id={task_id}")

    async def update_reply_segments_for_agent(
        self, task_id: str, agent_name: str, segments: list[dict[str, Any]]
    ) -> None:
        """整组覆盖写 replies.{agent}.segments  按时间顺序的段时间线"""
        await self.update_round_field(
            task_id, f"replies.{agent_name}.segments", segments
        )

    async def update_reply_for_agent(
        self,
        task_id: str,
        agent_name: str,
        reply: dict[str, Any],
    ) -> None:
        """覆盖写 replies.{agent} 整段 reply 状态

        reply 形如 {"state":"streaming|done|failed|cancelled","content":"...",
                   "segments":[...],"started_at":"...","finished_at":"...","error":"..."}
        agent name 不进 reply 字典  通过 dict key 表达  避免与 round.replies 形成冗余
        """
        await self.update_round_field(task_id, f"replies.{agent_name}", reply)

    async def select_reply(self, task_id: str, agent_name: str) -> None:
        """用户从多 agent 候选中选定一个作为正式回答

        校验:
            - round 不存在抛 KeyError
            - agent_name 不在 round.agents 抛 ValueError
            - replies[agent_name].state 不是 done 抛 ValueError 不允许选中失败/未完成的回答
            - 已选过则覆盖更新  允许用户改主意
        """
        round_doc = await self._db["rounds"].find_one(
            {"task_id": task_id},
            {"agents": 1, "replies": 1, "_id": 0},
        )
        if round_doc is None:
            raise KeyError(f"round 不存在 task_id={task_id}")

        agents = round_doc.get("agents") or []
        if agent_name not in agents:
            raise ValueError(
                f"agent_name {agent_name} 不在本轮候选 {agents}  无法选中"
            )

        replies = round_doc.get("replies") or {}
        target_reply = replies.get(agent_name) or {}
        if target_reply.get("state") != "done":
            raise ValueError(
                f"agent {agent_name} 的 reply 状态为 {target_reply.get('state')}  仅允许选中 done"
            )

        await self._db["rounds"].update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "selected_reply_agent": agent_name,
                    "updated_at": _utcnow(),
                }
            },
        )

    async def cancel_orphan_rounds(self, reason: str = "server_restart") -> int:
        """启动时清理孤儿 round  把"进行中"的 round 与其 replies 一并置为 cancelled

        进行中状态既包含新 4 态(pending/replying)  也兼容历史字面量:
            - thinking / think_done / decided  老 think 流程的中间态  历史快照
            - created / waiting_decision / failed  M0/M1 时期的旧值
            - replying  当前在用

        实现细节:
            - 用 $set 精准更新 顶层 state 不要走 replace_one
              否则 replies 之前的 content 会被整体覆盖丢失
            - replies 字段子项的 state 一并刷为 cancelled  避免页面里残留 streaming 卡片
            - cancel_reason / cancelled_at 同步落库 便于事后排查

        返回受影响的 round 数 用于启动日志
        """
        in_progress = [
            "pending",
            "replying",
            # 历史值兼容 数据库实际存的还是这些字面量
            "thinking",
            "think_done",
            "decided",
            "created",
            "waiting_decision",
        ]
        now = _utcnow()

        # 第一步 顶层 state 一次性原子更新
        result = await self._db["rounds"].update_many(
            {"state": {"$in": in_progress}},
            {
                "$set": {
                    "state": "cancelled",
                    "cancel_reason": reason,
                    "cancelled_at": now,
                    "updated_at": now,
                }
            },
        )
        modified = int(result.modified_count or 0)

        # 第二步 把每个 round 的 replies 子项 streaming/pending 全部刷成 cancelled
        # 用 update + arrayFilters 不行 因为 replies 是 dict 不是 array  改走遍历 + dot path
        # mongomock-motor 不支持 aggregation pipeline 下的 $map  所以这里逐 round 修
        async for r in self._db["rounds"].find(
            {"cancel_reason": reason, "cancelled_at": now},
            {"task_id": 1, "replies": 1, "_id": 0},
        ):
            replies = r.get("replies") or {}
            updates: dict[str, Any] = {}
            for agent_name, reply in replies.items():
                if not isinstance(reply, dict):
                    continue
                rstate = reply.get("state")
                if rstate in (None, "pending", "streaming"):
                    updates[f"replies.{agent_name}.state"] = "cancelled"
            if updates:
                await self._db["rounds"].update_one(
                    {"task_id": r["task_id"]},
                    {"$set": updates},
                )

        # 兼容老 round  顶层 reply 单字段  把 reply.state 也刷成 cancelled
        await self._db["rounds"].update_many(
            {
                "cancel_reason": reason,
                "cancelled_at": now,
                "reply": {"$type": "object"},
            },
            {"$set": {"reply.state": "cancelled"}},
        )
        return modified

    # =============================================================== Agents CRUD
    async def list_agents(self, *, owner_user_id: str | None = None) -> list[AgentRecord]:
        """按 name 升序列出 agent  owner_user_id 为 None 时列出全部（仅内部任务/系统调用使用）

        过滤规则：严格按 owner_user_id == 当前用户 不做 system 共享。
        """
        if owner_user_id is None:
            cursor = self._db["agents"].find({}, {"_id": 0}).sort("name", ASCENDING)
        else:
            cursor = (
                self._db["agents"]
                .find(
                    {"owner_user_id": owner_user_id},
                    {"_id": 0},
                )
                .sort("name", ASCENDING)
            )
        out: list[AgentRecord] = []
        async for doc in cursor:
            out.append(_agent_doc_to_record(doc))
        return out

    async def get_agent(self, name: str, *, owner_user_id: str) -> AgentRecord | None:
        # 严格按 owner_user_id 过滤 只返回属于当前用户的 agent
        doc = await self._db["agents"].find_one(
            {
                "name": name,
                "owner_user_id": owner_user_id,
            },
            {"_id": 0},
        )
        if doc is None:
            return None
        return _agent_doc_to_record(doc)

    async def create_agent(
        self,
        name: str | None,
        display_name: str,
        base_url: str,
        api_key: str,
        model: str,
        prompt: str,
        available_models: list[ModelCatalogEntry] | None = None,
        provider_type: str = "openai_compatible",
        *,
        owner_user_id: str,
    ) -> AgentRecord:
        """新建 agent  name 不传则自动生成 agent_<8位hex>  name 重复抛 ValueError

        display_name 若为空字符串 则回落用 name 充当显示名 避免前端列表显示空白
        """
        # 生成 / 校验内部稳定 name
        final_name = (name or "").strip() or _new_agent_name()

        existing = await self._db["agents"].find_one({"name": final_name}, {"_id": 0})
        if existing is not None:
            raise ValueError(f"agent 已存在 name={final_name}")

        now = _utcnow()
        models_list = _normalize_models(available_models) or []
        doc: dict[str, Any] = {
            "name": final_name,
            "owner_user_id": owner_user_id,
            "display_name": display_name.strip() or final_name,
            "provider_type": provider_type or "openai_compatible",
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "available_models": models_list,
            "prompt": prompt,
            "version": 1,
            "updated_at": now,
        }
        await self._db["agents"].insert_one(dict(doc))
        return _agent_doc_to_record(doc)

    async def delete_agent(self, name: str, *, owner_user_id: str) -> None:
        """删除 agent  若该 name 是当前 judge_target 抛 ValueError 路由层映射 409

        不存在或不属于当前用户抛 KeyError 路由层映射 404
        system agent 不可删除
        """
        existing = await self._db["agents"].find_one({"name": name}, {"_id": 0})
        if existing is None:
            raise KeyError(f"agent 不存在 name={name}")

        existing_owner = existing.get("owner_user_id", "system")
        if existing_owner == "system":
            raise ValueError(f"system agent 不可删除 name={name}")
        if existing_owner != owner_user_id:
            raise KeyError(f"agent 不存在 name={name}")

        # 校验是否被 judge 指针引用
        judge_doc = await self._db["settings"].find_one({"_id": _JUDGE_POINTER_DOC_ID})
        if judge_doc is not None and judge_doc.get(_JUDGE_POINTER_FIELD) == name:
            raise ValueError(f"agent {name} 仍是当前 judge target 无法删除 请先切换 judge")

        await self._db["agents"].delete_one({"name": name})

    async def upsert_agent(
        self,
        name: str,
        *,
        display_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        available_models: list | None = None,
        prompt: str | None = None,
        provider_type: str | None = None,
        owner_user_id: str,
    ) -> AgentRecord:
        """部分更新 agent  None 表示保留旧值  version 自增  不存在抛 KeyError

        system agent 不可更新
        新增 H7 配置回滚:
            - 写新值前 先把当前值整体归档到 agent_history 集合
            - 归档文档保留 完整字段 + archived_at archived_reason
        """
        existing = await self._db["agents"].find_one({"name": name}, {"_id": 0})
        if existing is None:
            raise KeyError(f"agent 不存在 name={name}")

        existing_owner = existing.get("owner_user_id", "system")
        if existing_owner == "system":
            raise ValueError(f"system agent 不可修改 name={name}")
        if existing_owner != owner_user_id:
            raise KeyError(f"agent 不存在 name={name}")

        now = _utcnow()
        # 归档旧值 让历史可追溯 reason 默认 upsert 由 routes 层调 revert 时传 revert
        archive_doc: dict[str, Any] = {
            "name": existing["name"],
            "display_name": existing.get("display_name") or existing["name"],
            "provider_type": existing.get("provider_type", "openai_compatible"),
            "base_url": existing.get("base_url", ""),
            "api_key": existing.get("api_key", ""),
            "model": existing.get("model", ""),
            "available_models": existing.get("available_models", []),
            "prompt": existing.get("prompt", ""),
            "version": int(existing.get("version", 1)),
            "archived_at": now,
            "archived_reason": "upsert",
        }
        await self._db["agent_history"].insert_one(archive_doc)

        new_version = int(existing.get("version", 1)) + 1
        updates: dict[str, Any] = {
            "version": new_version,
            "updated_at": now,
        }
        if display_name is not None:
            updates["display_name"] = display_name.strip() or existing["name"]
        if base_url is not None:
            updates["base_url"] = base_url
        if api_key is not None:
            updates["api_key"] = api_key
        if model is not None:
            updates["model"] = model
        if prompt is not None:
            updates["prompt"] = prompt
        if provider_type is not None:
            updates["provider_type"] = provider_type
        if available_models is not None:
            normalized = _normalize_models(available_models)
            if normalized is not None:
                updates["available_models"] = normalized

        await self._db["agents"].update_one({"name": name}, {"$set": updates})
        merged = {**existing, **updates}
        return _agent_doc_to_record(merged)

    # --------------------------------------------------------- Agent Avatar
    # 头像变更不触发 version 自增也不归档进 agent_history
    # 因为头像是展示数据  和 prompt/model/api_key 这些会影响 LLM 行为的字段不同
    async def set_agent_avatar(
        self, name: str, avatar: dict[str, Any]
    ) -> AgentRecord:
        """更新 agent 头像对象引用  不存在抛 KeyError"""
        existing = await self._db["agents"].find_one({"name": name}, {"_id": 0})
        if existing is None:
            raise KeyError(f"agent 不存在 name={name}")
        await self._db["agents"].update_one(
            {"name": name},
            {"$set": {"avatar": avatar, "avatar_data_url": None}},
        )
        merged = {**existing, "avatar": avatar, "avatar_data_url": None}
        return _agent_doc_to_record(merged)

    async def clear_agent_avatar(self, name: str) -> AgentRecord:
        """清掉 agent 头像  字段置 None  不存在抛 KeyError"""
        existing = await self._db["agents"].find_one({"name": name}, {"_id": 0})
        if existing is None:
            raise KeyError(f"agent 不存在 name={name}")
        await self._db["agents"].update_one(
            {"name": name},
            {"$set": {"avatar": None, "avatar_data_url": None}},
        )
        merged = {**existing, "avatar": None, "avatar_data_url": None}
        return _agent_doc_to_record(merged)

    # --------------------------------------------------------- Agent History
    async def list_agent_history(
        self, name: str, limit: int = 20
    ) -> list[dict]:
        """列出 agent 历史版本 按 version 降序 默认 20 条

        返回字典列表 字段含完整 agent 配置 + archived_at archived_reason
        """
        cursor = (
            self._db["agent_history"]
            .find({"name": name}, {"_id": 0})
            .sort("version", DESCENDING)
            .limit(max(1, int(limit)))
        )
        out: list[dict] = []
        async for doc in cursor:
            out.append(dict(doc))
        return out

    async def get_agent_history(
        self, name: str, version: int
    ) -> dict | None:
        """取指定 agent 的指定历史版本字典 不存在返回 None"""
        doc = await self._db["agent_history"].find_one(
            {"name": name, "version": int(version)}, {"_id": 0}
        )
        return dict(doc) if doc is not None else None

    # ------------------------------------------------------------- Judge 指针
    async def get_judge_target(self) -> str:
        """从 settings 集合读取 judge 指针 缺失则抛 KeyError 让上层提示先 seed"""
        doc = await self._db["settings"].find_one({"_id": _JUDGE_POINTER_DOC_ID})
        if doc is None or _JUDGE_POINTER_FIELD not in doc:
            raise KeyError("judge 指针未初始化 请先调用 seed_from_yaml 或 set_judge_target")
        return str(doc[_JUDGE_POINTER_FIELD])

    async def set_judge_target(self, agent_name: str) -> None:
        """设置 judge 指针 校验 agent_name 必须是已存在的 agent"""
        existing = await self._db["agents"].find_one({"name": agent_name}, {"_id": 0})
        if existing is None:
            raise KeyError(f"目标 agent 不存在 agent_name={agent_name}")
        await self._db["settings"].update_one(
            {"_id": _JUDGE_POINTER_DOC_ID},
            {"$set": {_JUDGE_POINTER_FIELD: agent_name, "updated_at": _utcnow()}},
            upsert=True,
        )

    # --------------------------------------------------------------- 数据迁移
    async def _migrate_legacy_agents(self) -> int:
        """老版本 agents 文档可能含 profile_name 字段 而 base_url/api_key 在
        provider_profiles 集合里 这里在启动时一次性迁移成新结构

        步骤:
            1. 找出所有含 profile_name 字段的 agent 文档
            2. 加载 provider_profiles 集合内全部 profile (按 name 索引)
            3. 把 profile 的 base_url/api_key/models 拷到对应 agent 文档
            4. 给 agent 文档补 display_name(缺失时用 name)
            5. 清空 provider_profiles 集合
            6. $unset profile_name 字段

        返回迁移条数 0 表示无历史数据需要处理
        """
        legacy_agents = await self._db["agents"].count_documents(
            {"profile_name": {"$exists": True}}
        )
        if legacy_agents == 0:
            return 0

        # 加载 profile 集合到内存 形成 name -> profile_doc 索引
        profiles_by_name: dict[str, dict[str, Any]] = {}
        async for p in self._db["provider_profiles"].find({}, {"_id": 0}):
            profiles_by_name[str(p.get("name", ""))] = p

        migrated = 0
        cursor = self._db["agents"].find(
            {"profile_name": {"$exists": True}}, {"_id": 0}
        )
        async for doc in cursor:
            agent_name = doc["name"]
            profile_name = doc.get("profile_name", "")
            profile = profiles_by_name.get(profile_name) or profiles_by_name.get("默认") or {}

            updates: dict[str, Any] = {}
            # 仅当 agent 文档自己缺这些字段时才用 profile 的值填充
            if "base_url" not in doc:
                updates["base_url"] = profile.get("base_url", "")
            if "api_key" not in doc:
                updates["api_key"] = profile.get("api_key", "")
            if "available_models" not in doc:
                updates["available_models"] = profile.get("models", []) or []
            if "provider_type" not in doc:
                updates["provider_type"] = profile.get(
                    "provider_type", "openai_compatible"
                )
            if not doc.get("display_name"):
                updates["display_name"] = agent_name

            await self._db["agents"].update_one(
                {"name": agent_name},
                {
                    "$set": updates,
                    "$unset": {"profile_name": "", "kind": ""},
                },
            )
            migrated += 1

        # 迁移后清空 provider_profiles 集合 后续运行不再依赖
        try:
            await self._db["provider_profiles"].delete_many({})
        except Exception:
            # mongomock 极端情况下集合不存在 忽略
            _logger.exception("清空 provider_profiles 失败 忽略")

        _logger.info("迁移历史 agents 完成", migrated=migrated)
        return migrated

    # ------------------------------------------------------------- MCP Servers
    async def list_mcp_servers(self, *, owner_user_id: str) -> list[McpServerConfig]:
        """列出当前用户的 MCP 服务器配置 按 name 升序"""
        cursor = self._db["mcp_servers"].find(
            {"owner_user_id": owner_user_id}, {"_id": 0}
        ).sort("name", ASCENDING)
        out: list[McpServerConfig] = []
        async for doc in cursor:
            out.append(McpServerConfig.model_validate(doc))
        return out

    async def get_mcp_server(self, name: str, *, owner_user_id: str) -> McpServerConfig | None:
        """获取单个 MCP 服务器 严格按 owner_user_id 过滤"""
        doc = await self._db["mcp_servers"].find_one(
            {"name": name, "owner_user_id": owner_user_id}, {"_id": 0}
        )
        if doc is None:
            return None
        return McpServerConfig.model_validate(doc)

    async def upsert_mcp_server(self, server: McpServerConfig, *, owner_user_id: str) -> McpServerConfig:
        """创建或全量覆盖单个 MCP 服务器配置 (name, owner_user_id) 唯一"""
        now = _utcnow()
        doc = server.model_dump(mode="json")
        doc["owner_user_id"] = owner_user_id
        doc["updated_at"] = now
        await self._db["mcp_servers"].replace_one(
            {"name": server.name, "owner_user_id": owner_user_id}, doc, upsert=True
        )
        return McpServerConfig.model_validate(doc)

    async def delete_mcp_server(self, name: str, *, owner_user_id: str) -> None:
        """删除 MCP 服务器 不存在或不属于当前用户抛 KeyError"""
        result = await self._db["mcp_servers"].delete_one(
            {"name": name, "owner_user_id": owner_user_id}
        )
        if result.deleted_count == 0:
            raise KeyError(f"MCP server 不存在或不属于当前用户 name={name}")

    # ------------------------------------------------------------- Skills
    async def list_skills(self, *, owner_user_id: str) -> list[SkillConfig]:
        """列出当前用户的 Skill 配置 按 name 升序"""
        cursor = self._db["skills"].find(
            {"owner_user_id": owner_user_id}, {"_id": 0}
        ).sort("name", ASCENDING)
        out: list[SkillConfig] = []
        async for doc in cursor:
            out.append(SkillConfig.model_validate(doc))
        return out

    async def get_skill(self, name: str, *, owner_user_id: str) -> SkillConfig | None:
        """获取单个 Skill 严格按 owner_user_id 过滤"""
        doc = await self._db["skills"].find_one(
            {"name": name, "owner_user_id": owner_user_id}, {"_id": 0}
        )
        if doc is None:
            return None
        return SkillConfig.model_validate(doc)

    async def upsert_skill(self, skill: SkillConfig, *, owner_user_id: str) -> SkillConfig:
        """创建或全量覆盖单个 Skill 配置 (name, owner_user_id) 唯一"""
        now = _utcnow()
        doc = skill.model_dump(mode="json")
        doc["owner_user_id"] = owner_user_id
        doc["updated_at"] = now
        await self._db["skills"].replace_one(
            {"name": skill.name, "owner_user_id": owner_user_id}, doc, upsert=True
        )
        return SkillConfig.model_validate(doc)

    async def delete_skill(self, name: str, *, owner_user_id: str) -> None:
        """删除 Skill 不存在或不属于当前用户抛 KeyError"""
        result = await self._db["skills"].delete_one(
            {"name": name, "owner_user_id": owner_user_id}
        )
        if result.deleted_count == 0:
            raise KeyError(f"skill 不存在或不属于当前用户 name={name}")

    # ------------------------------------------------------------- 数据迁移
    async def _migrate_global_mcp_and_skills(self) -> None:
        """将 settings 集合中旧全局 mcp_config / skills_config 迁移到新集合

        幂等: 目标集合已有数据则跳过
        旧全局配置直接丢弃 (不做 system 级共享) 用户需自行创建
        """
        # MCP 迁移: 如果 mcp_servers 集合为空 则把旧全局配置迁过来作为首个用户的资源
        # 但由于不做 system 级共享 旧全局配置无法归属到某个具体用户 直接丢弃
        # 只清理旧文档防止下次再检查
        mcp_count = await self._db["mcp_servers"].count_documents({})
        if mcp_count == 0:
            _logger.info("mcp_servers 集合为空 旧全局配置将被丢弃 用户需自行创建")
            # 删除旧的 settings mcp_config 文档 (可选 保守起见保留)
            # await self._db["settings"].delete_one({"_id": "mcp_config"})
        else:
            _logger.info("mcp_servers 集合已有数据 跳过迁移", count=mcp_count)

        # Skills 迁移: 同理 直接丢弃旧全局配置
        skills_count = await self._db["skills"].count_documents({})
        if skills_count == 0:
            _logger.info("skills 集合为空 旧全局配置将被丢弃 用户需自行创建")
        else:
            _logger.info("skills 集合已有数据 跳过迁移", count=skills_count)

    # --------------------------------------------------------------- Seed 注入
    async def seed_from_yaml(self, settings: Any) -> int:
        """首次启动从 yaml 注入种子 agents collection 已有数据时直接跳过

        参数 settings 形如 multichat.config.Settings 含 agents 字典与 judge 指针
        返回值是写入的 agent 条数 0 表示已 seed 过

        逻辑:
            1. 启动先跑数据迁移 把老 profile_name 模型升级到新数字员工模型
            2. 已存在 agents 文档则跳过 seed
            3. 否则按 yaml.agents 段每条注入完整字段 base_url/api_key 来自 yaml 顶层
            4. available_models 默认池 = yaml.agents 中所有 model 去重 + 4 条扩展

        注意:
            judge 指针即便已经存在也不在这里覆盖 完全由用户后续通过 set_judge_target 调
            首次种子默认值用 settings.judge.agent
        """
        # 数据迁移 仅对老 profile_name 数据生效 不影响新数据
        await self._migrate_legacy_agents()

        has_seed_config = bool(
            getattr(settings, "key", None)
            and getattr(settings, "base_url", None)
            and getattr(settings, "agents", None)
            and getattr(settings, "judge", None)
        )
        if not has_seed_config:
            _logger.info("未提供 yaml 种子配置 跳过 seed")
            return 0

        existing = await self._db["agents"].count_documents({})
        if existing > 0:
            _logger.info("agents 已存在 跳过 seed", existing=existing)
            return 0

        # 默认模型池 = yaml.agents 中出现的所有 model 去重 + 常用扩展模型
        # max_input_tokens 不在 seed 阶段写入  让用户在前端表单首次配置时显式填值
        # 读取阶段 _agent_doc_to_record 兜底 200000 保证服务能起 但 DB 里这个字段为空
        # 用户进入 agent 表单看到默认值后保存 才把真实值写进 DB
        yaml_models: list[str] = []
        for cfg in settings.agents.values():
            if cfg.model and cfg.model not in yaml_models:
                yaml_models.append(cfg.model)
        extra_models = ["deepseek-v3", "glm-4.5", "kimi-k2", "qwen-max"]
        for m in extra_models:
            if m not in yaml_models:
                yaml_models.append(m)
        models_pool = [
            {
                "model_id": m,
                "label": m,
            }
            for m in yaml_models
        ]

        now = _utcnow()
        docs: list[dict[str, Any]] = []
        for agent_name, agent_cfg in settings.agents.items():
            docs.append(
                {
                    "name": agent_name,
                    "display_name": agent_name,
                    "provider_type": "openai_compatible",
                    "base_url": settings.base_url,
                    "api_key": settings.key,
                    "model": agent_cfg.model,
                    "available_models": list(models_pool),
                    "prompt": agent_cfg.prompt,
                    "version": 1,
                    "updated_at": now,
                }
            )
        if docs:
            await self._db["agents"].insert_many(docs)

        # 同步写 judge 指针 仅当此前未设置时写入 避免误覆盖管理员后改的值
        # 这里走 update_one upsert 确保首次启动一定有指针可读
        await self._db["settings"].update_one(
            {"_id": _JUDGE_POINTER_DOC_ID},
            {
                "$setOnInsert": {
                    _JUDGE_POINTER_FIELD: settings.judge.agent,
                    "created_at": now,
                },
                "$set": {"updated_at": now},
            },
            upsert=True,
        )

        _logger.info("seed 注入完成", agents_written=len(docs), judge=settings.judge.agent)
        return len(docs)

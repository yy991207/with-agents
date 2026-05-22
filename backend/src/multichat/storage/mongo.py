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
from datetime import datetime, timezone
from typing import Any

import structlog
from pymongo import ASCENDING, DESCENDING

from ..core.models import (
    AgentRecord,
    ModelCatalogEntry,
    ProviderProfile,
    Round,
    Session,
    SessionMeta,
    TaskState,
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


def _strip_internal(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """剥离 mongo 内部 _id 字段 业务层不消费"""
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


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

        # agent_history 集合 按 (name, version 降序) 查最近版本快
        # version 不强制唯一 因为同一名字不同时间会有多版 但同名同 version 唯一
        await self._db["agent_history"].create_index(
            [("name", ASCENDING), ("version", DESCENDING)],
            name="idx_agent_history_name_version_desc",
        )

        # provider_profiles 集合  name 唯一  支持运行时切换 base_url + api_key
        await self._db["provider_profiles"].create_index(
            [("name", ASCENDING)], unique=True, name="uniq_profile_name"
        )

    # ============================================================ Sessions CRUD
    async def create_session(self, title: str | None = None) -> str:
        """新建会话 返回 string session_id"""
        session_id = _new_id()
        now = _utcnow()
        doc = {
            "session_id": session_id,
            "title": title or "",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        await self._db["sessions"].insert_one(doc)
        return session_id

    async def list_sessions(self, limit: int = 50) -> list[SessionMeta]:
        """按 updated_at 倒序拉最近会话列表 不含 rounds 详情"""
        cursor = (
            self._db["sessions"]
            .find({}, {"_id": 0})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        out: list[SessionMeta] = []
        async for doc in cursor:
            out.append(SessionMeta.model_validate(doc))
        return out

    async def get_session(self, session_id: str) -> Session | None:
        """单个会话详情 不内联 rounds 路由层视情况再 list_rounds"""
        doc = await self._db["sessions"].find_one({"session_id": session_id})
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

    async def delete_session(self, session_id: str) -> int:
        """删除 session 与其下所有 rounds 返回删除的 round 数

        约束:
            - session 不存在抛 KeyError 路由层映射 404
            - 若 session 下存在进行中的 round 抛 ValueError 路由层映射 409
              进行中状态包含 spec 新值 + 历史值 与 cancel_orphan_rounds 对齐
        """
        sess = await self._db["sessions"].find_one({"session_id": session_id})
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
        await self._db["sessions"].delete_one({"session_id": session_id})
        return int(rounds_res.deleted_count or 0)

    # =============================================================== Rounds CRUD
    async def create_round(
        self,
        session_id: str,
        user_message: str,
        user_mention: str | None,
    ) -> str:
        """创建一轮新提问 自动累计 round_index 返回 task_id"""
        # 先确认 session 存在 否则后续操作会留下孤儿 round
        session_doc = await self._db["sessions"].find_one({"session_id": session_id})
        if session_doc is None:
            raise KeyError(f"session 不存在 session_id={session_id}")

        # 计算下一个 round_index 简单查最大值 +1
        last = await self._db["rounds"].find_one(
            {"session_id": session_id},
            sort=[("round_index", DESCENDING)],
            projection={"round_index": 1, "_id": 0},
        )
        next_index = (last["round_index"] + 1) if last else 0

        task_id = _new_id()
        now = _utcnow()
        doc = {
            "task_id": task_id,
            "session_id": session_id,
            "round_index": next_index,
            "question": user_message,
            "user_mention": user_mention,
            "think_results": [],
            "chosen_agent": None,
            "reply_content": "",
            "state": TaskState.PENDING.value,
            "created_at": now,
            "updated_at": now,
        }
        await self._db["rounds"].insert_one(doc)
        # 同步 session updated_at 让会话列表能反映最近活动
        await self._db["sessions"].update_one(
            {"session_id": session_id}, {"$set": {"updated_at": now}}
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
        """按 dot path 局部更新 round 字段 例如 think_results.0.reason

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

    async def append_reply_chunk(self, task_id: str, chunk: str) -> None:
        """流式回复追加片段 用 aggregation pipeline update 原子拼接 reply_content

        节流写在 M2 reply 阶段实现 当前为基础原子写 失败抛 KeyError
        """
        result = await self._db["rounds"].update_one(
            {"task_id": task_id},
            [
                {
                    "$set": {
                        "reply_content": {
                            "$concat": [
                                {"$ifNull": ["$reply_content", ""]},
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

    async def cancel_orphan_rounds(self, reason: str = "server_restart") -> int:
        """启动时清理孤儿 round 把"进行中"状态的 round 一律置为 cancelled

        进行中状态既包含 spec 新值 也兼容历史字面量(created/waiting_decision):
            - pending / thinking / think_done / decided / replying  当前在用
            - created / waiting_decision  历史快照 H4 之前的写入
        旧值 failed 已是终态 不再清理 避免反复改写

        实现细节:
            - 用 $set 精准更新 顶层 state 不要走 replace_one
              否则 thinks/decision/reply 之前的 content 会被整体覆盖丢失
            - 嵌套字段 reply.state 单独走第二步 update_many
              用 $type:object 过滤掉 reply 为 null 的文档 避免 mongo 在非对象上写子字段报错
            - cancel_reason / cancelled_at 同步落库 便于事后排查

        返回受影响的 round 数 用于启动日志
        """
        in_progress = [
            "pending",
            "thinking",
            "think_done",
            "decided",
            "replying",
            # 历史值兼容 数据库实际存的还是这些字面量
            "created",
            "waiting_decision",
        ]
        now = _utcnow()

        # 第一步 顶层字段一次性原子更新 这一步的 modified_count 即受影响 round 数
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

        # 第二步 仅对 reply 是对象的文档把 reply.state 也置为 cancelled
        # 不能在第一步里直接 $set reply.state 因为 reply 为 null 时会报错
        # reply 不存在 / 为 null 则保持原状 反正不会有残留 streaming 影响
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
    async def list_agents(self) -> list[AgentRecord]:
        """按 name 升序列出所有 agent"""
        cursor = self._db["agents"].find({}, {"_id": 0}).sort("name", ASCENDING)
        out: list[AgentRecord] = []
        async for doc in cursor:
            out.append(AgentRecord.model_validate(doc))
        return out

    async def get_agent(self, name: str) -> AgentRecord | None:
        doc = await self._db["agents"].find_one({"name": name}, {"_id": 0})
        if doc is None:
            return None
        return AgentRecord.model_validate(doc)

    async def upsert_agent(
        self,
        name: str,
        model: str,
        prompt: str,
        kind: str = "agent",
        profile_name: str | None = None,
    ) -> AgentRecord:
        """新增或更新 agent 已存在则 version +1 反之初始化 version=1

        kind 字段固定为 agent 这里不开放第二个枚举值 保持集合纯净

        profile_name 可选传入语义:
            - None 旧记录已有则保留旧值 否则取默认值"默认"
            - 显式传字符串则覆盖落库 由路由层在调用前做存在性校验

        新增 H7 配置回滚:
            - 写新值前 先把当前值整体归档到 agent_history 集合
            - 归档文档保留 name model prompt version + archived_at archived_reason
              (含 profile_name 便于历史回滚也能回到旧 profile)
            - 仅当存在 current 才归档 首次插入不留历史(没有"上一版"可回滚)
        """
        existing = await self._db["agents"].find_one({"name": name}, {"_id": 0})
        now = _utcnow()
        if existing is None:
            # 新建路径 profile_name 缺省回落"默认"
            doc = {
                "name": name,
                "profile_name": profile_name or "默认",
                "model": model,
                "prompt": prompt,
                "kind": "agent",
                "version": 1,
                "updated_at": now,
            }
            await self._db["agents"].insert_one(dict(doc))
            return AgentRecord.model_validate(doc)

        # 归档旧值 让历史可追溯 reason 默认 upsert 由 routes 层调 revert 时传 revert
        archive_doc = {
            "name": existing["name"],
            "profile_name": existing.get("profile_name", "默认"),
            "model": existing["model"],
            "prompt": existing["prompt"],
            "version": int(existing.get("version", 1)),
            "archived_at": now,
            "archived_reason": "upsert",
        }
        await self._db["agent_history"].insert_one(archive_doc)

        new_version = int(existing.get("version", 1)) + 1
        # profile_name 处理: 传了用新值 没传保留旧值 旧值缺失兜底"默认"
        new_profile_name = profile_name if profile_name is not None else existing.get("profile_name", "默认")
        updates = {
            "profile_name": new_profile_name,
            "model": model,
            "prompt": prompt,
            "kind": "agent",
            "version": new_version,
            "updated_at": now,
        }
        await self._db["agents"].update_one({"name": name}, {"$set": updates})
        merged = {**existing, **updates}
        # 仅保留模型字段 防多余键漏到 pydantic 校验
        return AgentRecord.model_validate(
            {
                "name": name,
                "profile_name": merged["profile_name"],
                "model": merged["model"],
                "prompt": merged["prompt"],
                "kind": "agent",
                "version": merged["version"],
                "updated_at": merged["updated_at"],
            }
        )

    # --------------------------------------------------------- Agent History
    async def list_agent_history(
        self, name: str, limit: int = 20
    ) -> list[dict]:
        """列出 agent 历史版本 按 version 降序 默认 20 条

        返回字典列表 字段为 name model prompt version archived_at archived_reason
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

    # --------------------------------------------------------------- Seed 注入
    async def seed_from_yaml(self, settings: Any) -> int:
        """首次启动从 yaml 注入种子 agents collection 已有数据时直接跳过

        参数 settings 形如 multichat.config.Settings 含 agents 字典与 judge 指针
        返回值是写入的 agent 条数 0 表示已 seed 过

        新增逻辑:
            - 若 provider_profiles 集合为空 把 yaml 顶层 key/base_url 注入名为"默认" profile
            - 默认 profile 的 models 池 = yaml.agents 段中所有 agent.model 去重 + 几条常用扩展
            - agents seed 时给每条 record 写 profile_name="默认"

        注意:
            judge 指针即便已经存在也不在这里覆盖 完全由用户后续通过 set_judge_target 调
            首次种子默认值用 settings.judge.agent
        """
        # 默认 profile 注入: 仅当 provider_profiles 集合为空时执行
        # 已存在则视为运行时已经管理过 不再回写以免覆盖用户改动
        profile_existing = await self._db["provider_profiles"].count_documents({})
        if profile_existing == 0:
            now0 = _utcnow()
            # 默认模型池 = yaml.agents 中出现的所有 model 去重 + 常用扩展模型
            yaml_models: list[str] = []
            for cfg in settings.agents.values():
                if cfg.model and cfg.model not in yaml_models:
                    yaml_models.append(cfg.model)
            extra_models = ["deepseek-v3", "glm-4.5", "kimi-k2", "qwen-max"]
            for m in extra_models:
                if m not in yaml_models:
                    yaml_models.append(m)
            default_profile_doc = {
                "name": "默认",
                "provider_type": "openai_compatible",
                "base_url": settings.base_url,
                "api_key": settings.key,
                "models": [{"model_id": m, "label": m} for m in yaml_models],
                "version": 1,
                "updated_at": now0,
            }
            await self._db["provider_profiles"].insert_one(default_profile_doc)
            _logger.info(
                "seed 注入默认 profile",
                base_url=settings.base_url,
                models_count=len(yaml_models),
            )

        existing = await self._db["agents"].count_documents({})
        if existing > 0:
            # 数据迁移 老版本 agents 文档可能缺 profile_name 字段
            # 启动时一次性补齐为 "默认" 防止 delete_profile 引用 count 漏算
            missing = await self._db["agents"].update_many(
                {"profile_name": {"$exists": False}},
                {"$set": {"profile_name": "默认"}},
            )
            if missing.modified_count > 0:
                _logger.info(
                    "迁移 agents 补齐 profile_name 字段",
                    modified=missing.modified_count,
                )
            _logger.info("agents 已存在 跳过 seed", existing=existing)
            return 0

        now = _utcnow()
        docs: list[dict[str, Any]] = []
        for agent_name, agent_cfg in settings.agents.items():
            docs.append(
                {
                    "name": agent_name,
                    "profile_name": "默认",
                    "model": agent_cfg.model,
                    "prompt": agent_cfg.prompt,
                    "kind": "agent",
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

    # ============================================================ Profiles CRUD
    async def list_profiles(self) -> list[ProviderProfile]:
        """按 name 升序列出所有 profile"""
        cursor = self._db["provider_profiles"].find({}, {"_id": 0}).sort("name", ASCENDING)
        out: list[ProviderProfile] = []
        async for doc in cursor:
            out.append(ProviderProfile.model_validate(doc))
        return out

    async def get_profile(self, name: str) -> ProviderProfile | None:
        doc = await self._db["provider_profiles"].find_one({"name": name}, {"_id": 0})
        if doc is None:
            return None
        return ProviderProfile.model_validate(doc)

    async def create_profile(self, profile: ProviderProfile) -> ProviderProfile:
        """新建 profile 同名已存在抛 ValueError"""
        existing = await self._db["provider_profiles"].find_one({"name": profile.name}, {"_id": 0})
        if existing is not None:
            raise ValueError(f"profile {profile.name} 已存在")
        now = _utcnow()
        doc = {
            "name": profile.name,
            "provider_type": profile.provider_type,
            "base_url": profile.base_url,
            "api_key": profile.api_key,
            "models": [m.model_dump(mode="json") for m in profile.models],
            "version": 1,
            "updated_at": now,
        }
        await self._db["provider_profiles"].insert_one(dict(doc))
        return ProviderProfile.model_validate(doc)

    async def update_profile(
        self,
        name: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        models: list | None = None,
        provider_type: str | None = None,
    ) -> ProviderProfile:
        """局部更新 profile  仅更新非 None 字段  不存在抛 KeyError  version+1"""
        existing = await self._db["provider_profiles"].find_one({"name": name}, {"_id": 0})
        if existing is None:
            raise KeyError(f"profile 不存在 name={name}")

        now = _utcnow()
        updates: dict[str, Any] = {
            "version": int(existing.get("version", 1)) + 1,
            "updated_at": now,
        }
        if base_url is not None:
            updates["base_url"] = base_url
        if api_key is not None:
            updates["api_key"] = api_key
        if provider_type is not None:
            updates["provider_type"] = provider_type
        if models is not None:
            # 接收 ModelCatalogEntry 列表或 dict 列表 统一序列化
            normalized: list[dict[str, Any]] = []
            for m in models:
                if isinstance(m, ModelCatalogEntry):
                    normalized.append(m.model_dump(mode="json"))
                elif isinstance(m, dict):
                    normalized.append({"model_id": str(m.get("model_id", "")), "label": str(m.get("label", ""))})
                else:
                    raise TypeError(f"models 项类型不支持 {type(m).__name__}")
            updates["models"] = normalized

        await self._db["provider_profiles"].update_one({"name": name}, {"$set": updates})
        merged = {**existing, **updates}
        return ProviderProfile.model_validate(
            {
                "name": name,
                "provider_type": merged.get("provider_type", "openai_compatible"),
                "base_url": merged["base_url"],
                "api_key": merged["api_key"],
                "models": merged.get("models", []),
                "version": merged["version"],
                "updated_at": merged["updated_at"],
            }
        )

    async def delete_profile(self, name: str) -> None:
        """删除 profile  仍被任意 agent 引用时抛 ValueError 路由层映射 409"""
        ref_cnt = await self._db["agents"].count_documents({"profile_name": name})
        if ref_cnt > 0:
            raise ValueError(f"profile {name} 仍被 {ref_cnt} 个 agent 引用 无法删除")
        await self._db["provider_profiles"].delete_one({"name": name})

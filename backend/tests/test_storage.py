"""storage.mongo 单元测试 用 mongomock-motor 驱动 不依赖真实 mongodb

覆盖:
    - sessions CRUD
    - rounds CRUD 包含 round_index 自增 与 dot path 局部更新 reply 流式追加
    - agents seed_from_yaml 首次注入 + 二次跳过
    - create_agent / delete_agent / upsert_agent 数字员工增删改
    - judge 指针 get/set 往返
    - 历史 profile_name 数据迁移
    - ensure_indexes 不报错
"""

from __future__ import annotations

from typing import Any

import pytest
from mongomock_motor import AsyncMongoMockClient

from multichat.config import AgentConfig, JudgeConfig, MongoConfig, Settings
from multichat.core.models import ModelCatalogEntry, TaskState
from multichat.storage.mongo import MotorMongoStorage


def _build_settings() -> Settings:
    """构造一份 4 agent + 1 judge 的种子 settings 用于测试 seed"""
    return Settings(
        key="sk-test-xxxx-tail",
        base_url="https://example.com/v1",
        agents={
            "DeepSeek": AgentConfig(model="deepseek-test", prompt="深度思考"),
            "GLM": AgentConfig(model="glm-test", prompt="活泼"),
            "Kimi": AgentConfig(model="kimi-test", prompt="温柔"),
            "Qwen": AgentConfig(model="qwen-test", prompt="百科"),
        },
        judge=JudgeConfig(agent="GLM", prompt="你是裁判"),
        mongo=MongoConfig(uri="mongodb://localhost:27017", db="multi_chat_test"),
    )


@pytest.fixture
async def storage() -> Any:
    """每个用例一个新 mongomock 客户端 ensure_indexes 完成后交给用例"""
    client = AsyncMongoMockClient()
    s = MotorMongoStorage.from_client(client, "multi_chat_test")
    await s.ensure_indexes()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_ensure_indexes_idempotent(storage: MotorMongoStorage) -> None:
    """重复 ensure_indexes 不报错"""
    await storage.ensure_indexes()
    await storage.ensure_indexes()


@pytest.mark.asyncio
async def test_sessions_crud(storage: MotorMongoStorage) -> None:
    """sessions 完整 CRUD 路径"""
    sid = await storage.create_session(title="第一个会话")
    assert isinstance(sid, str) and len(sid) == 32

    got = await storage.get_session(sid)
    assert got is not None
    assert got.session_id == sid
    assert got.title == "第一个会话"

    await storage.update_session_meta(sid, title="改名后")
    got2 = await storage.get_session(sid)
    assert got2 is not None
    assert got2.title == "改名后"

    sid2 = await storage.create_session()
    items = await storage.list_sessions(limit=10)
    assert {x.session_id for x in items} == {sid, sid2}
    # 默认按 updated_at 倒序 sid2 在 sid 之后创建 应排在前面
    assert items[0].session_id in {sid, sid2}

    with pytest.raises(KeyError):
        await storage.update_session_meta("not-exist", title="x")


@pytest.mark.asyncio
async def test_rounds_crud(storage: MotorMongoStorage) -> None:
    """rounds 完整 CRUD 含 round_index 自增 字段局部更新 状态机推进 reply 追加"""
    sid = await storage.create_session(title="t1")

    task_id_a = await storage.create_round(sid, "你好", None)
    task_id_b = await storage.create_round(sid, "再问一句", "@DeepSeek")

    rounds = await storage.list_rounds(sid)
    assert [r.round_index for r in rounds] == [0, 1]
    assert rounds[1].user_mention == "@DeepSeek"
    assert rounds[0].state == TaskState.PENDING

    await storage.update_round_state(task_id_a, TaskState.THINKING)
    a = await storage.get_round(task_id_a)
    assert a is not None
    assert a.state == TaskState.THINKING

    # 局部更新某个 think_results 整体
    await storage.update_round_field(
        task_id_a,
        "think_results",
        [{"agent_name": "GLM", "reason": "我懂这个", "latency_ms": 120, "error": None}],
    )
    a2 = await storage.get_round(task_id_a)
    assert a2 is not None
    assert len(a2.think_results) == 1
    assert a2.think_results[0].agent_name == "GLM"

    # reply 流式拼接
    await storage.append_reply_chunk(task_id_a, "Hello ")
    await storage.append_reply_chunk(task_id_a, "world")
    a3 = await storage.get_round(task_id_a)
    assert a3 is not None
    assert a3.reply_content == "Hello world"

    # 异常路径
    with pytest.raises(KeyError):
        await storage.create_round("missing-session", "q", None)
    with pytest.raises(KeyError):
        await storage.update_round_state("missing-task", TaskState.DONE)
    with pytest.raises(KeyError):
        await storage.append_reply_chunk("missing-task", "x")
    assert await storage.get_round("missing-task") is None
    # task_id_b 存在但未触动
    b = await storage.get_round(task_id_b)
    assert b is not None and b.round_index == 1


@pytest.mark.asyncio
async def test_seed_from_yaml_first_then_skip(storage: MotorMongoStorage) -> None:
    """首次 seed 注入 4 条 二次直接跳过 返回 0  agent 字段含完整数字员工"""
    settings = _build_settings()

    written = await storage.seed_from_yaml(settings)
    assert written == 4

    agents = await storage.list_agents()
    assert {a.name for a in agents} == {"DeepSeek", "GLM", "Kimi", "Qwen"}
    # seed 后所有 agent 都有 base_url/api_key/model/prompt
    for a in agents:
        assert a.base_url == settings.base_url
        assert a.api_key == settings.key
        assert a.display_name == a.name
        assert a.version == 1
        # available_models 池含 yaml 中的 model 与扩展模型
        ids = {m.model_id for m in a.available_models}
        for cfg in settings.agents.values():
            assert cfg.model in ids
        assert {"deepseek-v3", "glm-4.5", "kimi-k2", "qwen-max"} <= ids

    # 二次调用应跳过
    written2 = await storage.seed_from_yaml(settings)
    assert written2 == 0

    # judge 指针应已写入并指向种子默认值
    target = await storage.get_judge_target()
    assert target == "GLM"


@pytest.mark.asyncio
async def test_upsert_agent_bumps_version(storage: MotorMongoStorage) -> None:
    """改一次 agent 的 prompt version 应 +1 同时 updated_at 刷新"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)

    before = await storage.get_agent("GLM")
    assert before is not None and before.version == 1

    updated = await storage.upsert_agent(
        "GLM", model="glm-new", prompt="活泼但更专业"
    )
    assert updated.version == 2
    assert updated.model == "glm-new"
    assert updated.prompt == "活泼但更专业"
    # 没传的字段保持旧值
    assert updated.base_url == before.base_url
    assert updated.api_key == before.api_key

    # 再次 upsert 继续 +1
    updated2 = await storage.upsert_agent("GLM", model="glm-new2", prompt="再改一次")
    assert updated2.version == 3


@pytest.mark.asyncio
async def test_upsert_unknown_agent_raises(storage: MotorMongoStorage) -> None:
    """upsert 不存在的 agent 抛 KeyError 由路由层映射 404"""
    with pytest.raises(KeyError):
        await storage.upsert_agent("NoSuch", display_name="x")


@pytest.mark.asyncio
async def test_judge_pointer_set_get(storage: MotorMongoStorage) -> None:
    """judge 指针 set/get 往返 切换到另一个 agent 并校验"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)

    assert await storage.get_judge_target() == "GLM"

    await storage.set_judge_target("DeepSeek")
    assert await storage.get_judge_target() == "DeepSeek"

    # 设置一个不存在的 agent 应抛
    with pytest.raises(KeyError):
        await storage.set_judge_target("NotExist")


@pytest.mark.asyncio
async def test_judge_pointer_uninitialized() -> None:
    """完全未 seed 时 get_judge_target 抛 KeyError 提示先 seed"""
    client = AsyncMongoMockClient()
    s = MotorMongoStorage.from_client(client, "multi_chat_test")
    await s.ensure_indexes()
    with pytest.raises(KeyError):
        await s.get_judge_target()


@pytest.mark.asyncio
async def test_seed_does_not_overwrite_existing_judge(
    storage: MotorMongoStorage,
) -> None:
    """seed 走过一次后 用户改了 judge 指针 再次启动 seed 不应回写默认值"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)
    await storage.set_judge_target("Kimi")
    # 模拟二次启动
    await storage.seed_from_yaml(settings)
    assert await storage.get_judge_target() == "Kimi"


@pytest.mark.asyncio
async def test_cancel_orphan_rounds(storage: MotorMongoStorage) -> None:
    """启动孤儿清理: 进行中状态 + 历史值 全部置 cancelled DONE 状态保持不变"""
    sid = await storage.create_session(title="孤儿场景")

    # 准备 3 条 round 状态分别为 thinking / done / replying
    tid_thinking = await storage.create_round(sid, "q1", None)
    tid_done = await storage.create_round(sid, "q2", None)
    tid_replying = await storage.create_round(sid, "q3", None)
    await storage.update_round_state(tid_thinking, TaskState.THINKING)
    await storage.update_round_state(tid_done, TaskState.DONE)
    await storage.update_round_state(tid_replying, TaskState.REPLYING)

    # replying 状态额外塞一条嵌套 reply 验证 cancel_orphan_rounds 不会覆盖 content
    await storage.update_round_field(
        tid_replying,
        "reply",
        {"agent": "GLM", "state": "streaming", "content": "前缀"},
    )

    # 直接往 mongo 塞一条历史字面量 state="created" 的 round 模拟 H4 之前的存量
    legacy_sid = await storage.create_session(title="历史会话")
    legacy_tid = "legacy_task_id_0001"
    # 用底层 _db 直接 insert 绕过 create_round 的新值默认
    await storage._db["rounds"].insert_one(
        {
            "task_id": legacy_tid,
            "session_id": legacy_sid,
            "round_index": 0,
            "question": "历史问句",
            "user_mention": None,
            "think_results": [],
            "chosen_agent": None,
            "reply_content": "",
            "state": "created",
            "created_at": _utcnow_for_test(),
            "updated_at": _utcnow_for_test(),
        }
    )

    affected = await storage.cancel_orphan_rounds(reason="server_restart")
    # thinking + replying + legacy(created) 三条 done 不动
    assert affected == 3

    r_thinking = await storage.get_round(tid_thinking)
    r_done = await storage.get_round(tid_done)
    r_replying = await storage.get_round(tid_replying)
    r_legacy = await storage.get_round(legacy_tid)

    assert r_thinking is not None and r_thinking.state == TaskState.CANCELLED
    assert r_replying is not None and r_replying.state == TaskState.CANCELLED
    assert r_done is not None and r_done.state == TaskState.DONE
    # legacy state="created" 已被 cancel_orphan_rounds 改成 cancelled
    assert r_legacy is not None and r_legacy.state == TaskState.CANCELLED

    # 校验嵌套 reply.state 也被改 而 reply.content 不能被吞掉
    raw = await storage._db["rounds"].find_one({"task_id": tid_replying})
    assert raw is not None
    assert raw["state"] == "cancelled"
    assert raw["reply"]["state"] == "cancelled"
    assert raw["reply"]["content"] == "前缀"
    assert raw["cancel_reason"] == "server_restart"
    assert raw.get("cancelled_at") is not None

    # 二次调用不应再有 round 命中 因为它们已经是 cancelled
    again = await storage.cancel_orphan_rounds(reason="server_restart")
    assert again == 0


@pytest.mark.asyncio
async def test_round_state_legacy_migration(storage: MotorMongoStorage) -> None:
    """直接落库 state=created 时 get_round 应映射成 TaskState.PENDING"""
    sid = await storage.create_session(title="legacy")
    tid = "legacy_state_round_id"
    await storage._db["rounds"].insert_one(
        {
            "task_id": tid,
            "session_id": sid,
            "round_index": 0,
            "question": "你好",
            "user_mention": None,
            "think_results": [],
            "chosen_agent": None,
            "reply_content": "",
            "state": "created",
            "created_at": _utcnow_for_test(),
            "updated_at": _utcnow_for_test(),
        }
    )
    r = await storage.get_round(tid)
    assert r is not None
    assert r.state == TaskState.PENDING

    # waiting_decision -> think_done
    tid2 = "legacy_state_round_id_2"
    await storage._db["rounds"].insert_one(
        {
            "task_id": tid2,
            "session_id": sid,
            "round_index": 1,
            "question": "再问一句",
            "user_mention": None,
            "think_results": [],
            "chosen_agent": None,
            "reply_content": "",
            "state": "waiting_decision",
            "created_at": _utcnow_for_test(),
            "updated_at": _utcnow_for_test(),
        }
    )
    r2 = await storage.get_round(tid2)
    assert r2 is not None
    assert r2.state == TaskState.THINK_DONE

    # failed -> cancelled
    tid3 = "legacy_state_round_id_3"
    await storage._db["rounds"].insert_one(
        {
            "task_id": tid3,
            "session_id": sid,
            "round_index": 2,
            "question": "失败的轮",
            "user_mention": None,
            "think_results": [],
            "chosen_agent": None,
            "reply_content": "",
            "state": "failed",
            "created_at": _utcnow_for_test(),
            "updated_at": _utcnow_for_test(),
        }
    )
    r3 = await storage.get_round(tid3)
    assert r3 is not None
    assert r3.state == TaskState.CANCELLED


# --------------------------------------------------------------- agent_history
@pytest.mark.asyncio
async def test_upsert_archives_old_version(storage: MotorMongoStorage) -> None:
    """upsert 三次后 history 应有 2 条(v1 v2) current 是 v3"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)
    # seed 写入 v1 不归档(首次插入无前值)
    await storage.upsert_agent("GLM", model="glm-1", prompt="活泼一点点")  # v2 归档 v1
    await storage.upsert_agent("GLM", model="glm-2", prompt="活泼专业一点")  # v3 归档 v2

    cur = await storage.get_agent("GLM")
    assert cur is not None and cur.version == 3 and cur.model == "glm-2"
    history = await storage.list_agent_history("GLM")
    versions = [int(h["version"]) for h in history]
    # 降序排列 含 v2 和 v1
    assert versions == [2, 1]
    # 内容正确归档
    v2 = [h for h in history if h["version"] == 2][0]
    assert v2["model"] == "glm-1"
    assert v2["prompt"] == "活泼一点点"
    assert v2["archived_reason"] == "upsert"


@pytest.mark.asyncio
async def test_list_agent_history_descending(storage: MotorMongoStorage) -> None:
    """list_agent_history 必须按 version 降序"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)
    for i in range(5):
        await storage.upsert_agent("Kimi", model=f"kimi-{i}", prompt=f"prompt-{i}-长一点")
    history = await storage.list_agent_history("Kimi", limit=10)
    versions = [int(h["version"]) for h in history]
    assert versions == sorted(versions, reverse=True)
    assert len(versions) == 5  # v1 v2 v3 v4 v5 都被归档


@pytest.mark.asyncio
async def test_get_agent_history_by_version(storage: MotorMongoStorage) -> None:
    """精确按 version 查 取不到返 None"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)
    await storage.upsert_agent("Qwen", model="qwen-2", prompt="百科加详细")
    h1 = await storage.get_agent_history("Qwen", 1)
    assert h1 is not None
    assert h1["model"] == "qwen-test"  # seed 时模型
    assert h1["prompt"] == "百科"
    # 不存在版本返 None
    assert await storage.get_agent_history("Qwen", 99) is None
    # 不存在 agent 返 None
    assert await storage.get_agent_history("NoSuchAgent", 1) is None


def _utcnow_for_test():
    """测试辅助 直接生成带时区的当前 UTC 时间 与 storage 一致"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# --------------------------------------------------------------- delete_session
@pytest.mark.asyncio
async def test_delete_session_with_rounds_ok(storage: MotorMongoStorage) -> None:
    """正常路径 session 下有 done 终态 round 删除应连带清掉所有 rounds"""
    sid = await storage.create_session(title="待删")
    tid_a = await storage.create_round(sid, "q1", None)
    tid_b = await storage.create_round(sid, "q2", None)
    # 全部置 DONE 状态 满足"无进行中"条件
    await storage.update_round_state(tid_a, TaskState.DONE)
    await storage.update_round_state(tid_b, TaskState.DONE)

    deleted = await storage.delete_session(sid)
    assert deleted == 2
    assert await storage.get_session(sid) is None
    # rounds 也应被清理
    rounds = await storage.list_rounds(sid)
    assert rounds == []


@pytest.mark.asyncio
async def test_delete_session_not_found_raises_keyerror(storage: MotorMongoStorage) -> None:
    """session 不存在 抛 KeyError 路由层映射 404"""
    with pytest.raises(KeyError):
        await storage.delete_session("no-such-session-id")


@pytest.mark.asyncio
async def test_delete_session_active_round_raises_valueerror(
    storage: MotorMongoStorage,
) -> None:
    """有进行中 round 抛 ValueError 路由层映射 409"""
    sid = await storage.create_session(title="活动会话")
    tid = await storage.create_round(sid, "q1", None)
    # 默认状态就是 PENDING 算进行中
    with pytest.raises(ValueError):
        await storage.delete_session(sid)
    # 历史值 created 也应被识别
    sid2 = await storage.create_session(title="历史活动")
    await storage._db["rounds"].insert_one(
        {
            "task_id": "legacy-active-tid",
            "session_id": sid2,
            "round_index": 0,
            "question": "q",
            "user_mention": None,
            "think_results": [],
            "chosen_agent": None,
            "reply_content": "",
            "state": "created",
            "created_at": _utcnow_for_test(),
            "updated_at": _utcnow_for_test(),
        }
    )
    with pytest.raises(ValueError):
        await storage.delete_session(sid2)
    # 把它置为 cancelled 后应可删
    await storage._db["rounds"].update_one(
        {"task_id": "legacy-active-tid"}, {"$set": {"state": "cancelled"}}
    )
    deleted = await storage.delete_session(sid2)
    assert deleted == 1
    # 原 session sid 把它的 round 切换到 cancelled 也应允许删
    await storage.update_round_state(tid, TaskState.CANCELLED)
    deleted2 = await storage.delete_session(sid)
    assert deleted2 == 1


# =============================================================== Agent CRUD 测试
@pytest.mark.asyncio
async def test_create_agent_with_explicit_name(storage: MotorMongoStorage) -> None:
    """显式传 name 时 应按指定 name 创建"""
    rec = await storage.create_agent(
        name="custom_name",
        display_name="自定义员工",
        base_url="https://api.example.com/v1",
        api_key="sk-tail-9999",
        model="gpt-4",
        prompt="你是一个测试员工",
    )
    assert rec.name == "custom_name"
    assert rec.display_name == "自定义员工"
    assert rec.api_key == "sk-tail-9999"
    assert rec.version == 1


@pytest.mark.asyncio
async def test_create_agent_auto_id(storage: MotorMongoStorage) -> None:
    """name 不传时应自动生成 agent_<8位hex>"""
    rec = await storage.create_agent(
        name=None,
        display_name="自动 ID 员工",
        base_url="https://api.example.com/v1",
        api_key="sk-auto-tail",
        model="gpt-4",
        prompt="你是一个测试员工",
    )
    assert rec.name.startswith("agent_")
    assert len(rec.name) == len("agent_") + 8
    # 再创建一个 应该 name 不同
    rec2 = await storage.create_agent(
        name=None,
        display_name="员工2",
        base_url="https://api.example.com/v1",
        api_key="sk-auto-tail",
        model="gpt-4",
        prompt="你是一个测试员工",
    )
    assert rec.name != rec2.name


@pytest.mark.asyncio
async def test_create_agent_duplicate_name_raises(
    storage: MotorMongoStorage,
) -> None:
    """显式 name 重复抛 ValueError 路由层映射 409"""
    await storage.create_agent(
        name="dup",
        display_name="原",
        base_url="https://x.example.com/v1",
        api_key="sk-dup-tail",
        model="m1",
        prompt="原 prompt",
    )
    with pytest.raises(ValueError):
        await storage.create_agent(
            name="dup",
            display_name="后",
            base_url="https://y.example.com/v1",
            api_key="sk-dup-tail2",
            model="m2",
            prompt="后 prompt",
        )


@pytest.mark.asyncio
async def test_delete_agent_judge_referenced_raises(
    storage: MotorMongoStorage,
) -> None:
    """删 judge target 抛 ValueError 路由层映射 409"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)
    # judge target 默认 GLM
    with pytest.raises(ValueError):
        await storage.delete_agent("GLM")
    # 切换 judge 后 应可删
    await storage.set_judge_target("DeepSeek")
    await storage.delete_agent("GLM")
    assert await storage.get_agent("GLM") is None


@pytest.mark.asyncio
async def test_delete_agent_unknown_raises_keyerror(
    storage: MotorMongoStorage,
) -> None:
    """删除不存在的 agent 抛 KeyError 路由层映射 404"""
    with pytest.raises(KeyError):
        await storage.delete_agent("not-exist")


@pytest.mark.asyncio
async def test_upsert_partial_keeps_other_fields(storage: MotorMongoStorage) -> None:
    """upsert 仅传 display_name 时 base_url/api_key/model 等保留"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)
    before = await storage.get_agent("GLM")
    assert before is not None

    upd = await storage.upsert_agent("GLM", display_name="可爱小G")
    assert upd.display_name == "可爱小G"
    assert upd.base_url == before.base_url
    assert upd.api_key == before.api_key
    assert upd.model == before.model
    assert upd.prompt == before.prompt
    assert upd.version == 2


@pytest.mark.asyncio
async def test_upsert_available_models(storage: MotorMongoStorage) -> None:
    """upsert 修改 available_models 应替换列表 接受 dict 或 ModelCatalogEntry"""
    settings = _build_settings()
    await storage.seed_from_yaml(settings)

    upd = await storage.upsert_agent(
        "GLM",
        available_models=[
            ModelCatalogEntry(model_id="m-A", label="模型A"),
            {"model_id": "m-B", "label": "模型B"},
        ],
    )
    assert {m.model_id for m in upd.available_models} == {"m-A", "m-B"}


@pytest.mark.asyncio
async def test_migrate_legacy_agents(storage: MotorMongoStorage) -> None:
    """老结构 agents 含 profile_name + provider_profiles 集合的数据 启动迁移后

    1. agents 文档不再有 profile_name
    2. agents 字段含 base_url / api_key / available_models
    3. provider_profiles 集合被清空
    """
    # 手工塞入 1 条老 agent 文档
    await storage._db["agents"].insert_one(
        {
            "name": "OldAgent",
            "profile_name": "默认",
            "model": "old-model",
            "prompt": "老 prompt",
            "kind": "agent",
            "version": 1,
        }
    )
    # 同步塞一条 provider_profiles 老数据
    await storage._db["provider_profiles"].insert_one(
        {
            "name": "默认",
            "provider_type": "openai_compatible",
            "base_url": "https://legacy.example.com/v1",
            "api_key": "sk-legacy-tail",
            "models": [{"model_id": "old-model", "label": "old-model"}],
            "version": 1,
        }
    )

    settings = _build_settings()
    # seed_from_yaml 内部会先迁移 然后因为 agents 已有数据 跳过 seed
    await storage.seed_from_yaml(settings)

    # 1. agents 文档已无 profile_name 字段 含 base_url / api_key / available_models
    raw = await storage._db["agents"].find_one({"name": "OldAgent"})
    assert raw is not None
    assert "profile_name" not in raw
    assert raw["base_url"] == "https://legacy.example.com/v1"
    assert raw["api_key"] == "sk-legacy-tail"
    assert raw.get("available_models") == [
        {"model_id": "old-model", "label": "old-model"}
    ]
    assert raw.get("display_name") == "OldAgent"

    # 2. provider_profiles 集合已被清空
    cnt = await storage._db["provider_profiles"].count_documents({})
    assert cnt == 0

    # 3. 通过 list_agents 也可以读出来 字段完整
    rec = await storage.get_agent("OldAgent")
    assert rec is not None
    assert rec.base_url == "https://legacy.example.com/v1"
    assert rec.display_name == "OldAgent"

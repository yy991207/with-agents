"""storage.mongo 单元测试 用 mongomock-motor 驱动 不依赖真实 mongodb

覆盖:
    - sessions CRUD
    - rounds CRUD 包含 round_index 自增 与 dot path 局部更新 reply 流式追加
    - agents seed_from_yaml 首次注入 + 二次跳过
    - upsert_agent 改 prompt 后 version +1
    - judge 指针 get/set 往返
    - ensure_indexes 不报错
"""

from __future__ import annotations

from typing import Any

import pytest
from mongomock_motor import AsyncMongoMockClient

from multichat.config import AgentConfig, JudgeConfig, MongoConfig, Settings
from multichat.core.models import TaskState
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
    assert rounds[0].state == TaskState.CREATED

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
    """首次 seed 注入 4 条 二次直接跳过 返回 0"""
    settings = _build_settings()

    written = await storage.seed_from_yaml(settings)
    assert written == 4

    agents = await storage.list_agents()
    assert {a.name for a in agents} == {"DeepSeek", "GLM", "Kimi", "Qwen"}
    assert all(a.kind == "agent" and a.version == 1 for a in agents)

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
        name="GLM",
        model="glm-new",
        prompt="活泼但更专业",
    )
    assert updated.version == 2
    assert updated.model == "glm-new"
    assert updated.prompt == "活泼但更专业"

    # 再次 upsert 继续 +1
    updated2 = await storage.upsert_agent(
        name="GLM",
        model="glm-new2",
        prompt="再改一次",
    )
    assert updated2.version == 3

    # 新 agent 通过 upsert 也能进 version 从 1 起
    fresh = await storage.upsert_agent(
        name="LocalLLM",
        model="local-1",
        prompt="本地模型",
    )
    assert fresh.version == 1
    assert fresh.kind == "agent"
    assert (await storage.get_agent("LocalLLM")) is not None


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

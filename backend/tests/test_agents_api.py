"""agents CRUD API 路由单元测试 用 mongomock-motor + httpx ASGITransport

被测对象
    - GET  /api/agents
    - PUT  /api/agents/{name}
    - PUT  /api/judge

不走完整 lifespan 而是手工挂 storage 与 registry 到 app.state
DeepAgentRegistry 真实 build 也不发网络请求 ChatOpenAI 仅在 ainvoke 时才触网
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from multichat.config import (
    AgentConfig,
    JudgeConfig,
    MongoConfig,
    RuntimeConfig,
    Settings,
)
from multichat.llm.deep_agents import build_registry
from multichat.routes.agents import router as agents_router
from multichat.storage.mongo import MotorMongoStorage


def _settings() -> Settings:
    """构造测试用最小有效 Settings"""
    return Settings(
        key="sk-test-tail",
        base_url="https://example.com/v1",
        agents={
            "DeepSeek": AgentConfig(model="deepseek-test", prompt="深度思考"),
            "GLM": AgentConfig(model="glm-test", prompt="活泼"),
            "Kimi": AgentConfig(model="kimi-test", prompt="温柔"),
            "Qwen": AgentConfig(model="qwen-test", prompt="百科"),
        },
        judge=JudgeConfig(agent="GLM", prompt="你是裁判"),
        mongo=MongoConfig(),
        runtime=RuntimeConfig(),
    )


@pytest.fixture
async def app_client() -> AsyncIterator[tuple[AsyncClient, MotorMongoStorage, object]]:
    """fixture 拼装一个最小 FastAPI 实例 仅挂 agents router

    返回三元组 (client, storage, registry) 测试用例可以直接拿到 storage 对比 DB
    """
    settings = _settings()
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "multi_chat_test")
    await storage.ensure_indexes()
    await storage.seed_from_yaml(settings)

    registry = build_registry(settings)
    records = await storage.list_agents()
    await registry.initialize(records)

    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.deep_agents = registry
    app.include_router(agents_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, storage, registry

    await storage.close()


@pytest.mark.asyncio
async def test_list_agents_after_seed(app_client: tuple[AsyncClient, MotorMongoStorage, object]) -> None:
    """seed 后 GET /api/agents 应返回 4 条 + judge_target=GLM"""
    ac, _storage, _reg = app_client
    resp = await ac.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    names = {a["name"] for a in data["agents"]}
    assert names == {"DeepSeek", "GLM", "Kimi", "Qwen"}
    # 默认 judge 指针来自 yaml.judge.agent
    assert data["judge_target"] == "GLM"
    # 字段完整性
    sample = data["agents"][0]
    assert {"name", "model", "prompt", "version", "updated_at"} <= set(sample.keys())
    # version 都是 1 因 seed 初始化
    assert all(a["version"] == 1 for a in data["agents"])


@pytest.mark.asyncio
async def test_update_agent_prompt(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """PUT /api/agents/GLM 改 prompt version 升到 2 且 registry 实例换新"""
    ac, storage, registry = app_client

    old_think = registry.get("GLM", "think")  # type: ignore[attr-defined]
    old_reply = registry.get("GLM", "reply")  # type: ignore[attr-defined]

    resp = await ac.put(
        "/api/agents/GLM",
        json={"prompt": "活泼但更严谨一点表达"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "GLM"
    assert body["version"] == 2
    assert body["reloaded"] is True

    # registry 实例已经换新
    new_think = registry.get("GLM", "think")  # type: ignore[attr-defined]
    new_reply = registry.get("GLM", "reply")  # type: ignore[attr-defined]
    assert new_think is not old_think
    assert new_reply is not old_reply

    # DB 内容也更新
    rec = await storage.get_agent("GLM")
    assert rec is not None and rec.prompt == "活泼但更严谨一点表达"


@pytest.mark.asyncio
async def test_update_agent_unknown_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """改不存在的 agent 应 404 无法新增"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/agents/Unknown", json={"prompt": "你好世界你好"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_agent_empty_body_400(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """body 不传 model/prompt 返回 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/agents/GLM", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_agent_short_prompt_400(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """prompt 长度不足 5 字 返回 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/agents/GLM", json={"prompt": "ab"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_agent_version_conflict_409(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """expected_version 与服务端不一致 返回 409"""
    ac, _s, _r = app_client
    resp = await ac.put(
        "/api/agents/GLM",
        json={"prompt": "活泼但更严谨一点", "expected_version": 999},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_judge(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """PUT /api/judge target=Kimi 后 GET /api/agents 的 judge_target=Kimi"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/judge", json={"target": "Kimi"})
    assert resp.status_code == 204

    resp2 = await ac.get("/api/agents")
    assert resp2.status_code == 200
    assert resp2.json()["judge_target"] == "Kimi"


@pytest.mark.asyncio
async def test_update_judge_unknown_400(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """target 不在已有 agent 中 返回 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/judge", json={"target": "Unknown"})
    assert resp.status_code == 400


# ------------------------------------------------------- 配置历史/回滚
@pytest.mark.asyncio
async def test_list_agent_history_returns_versions(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """连续 update 后 history 包含每次旧版本 按 version 降序"""
    ac, _s, _r = app_client
    # 第一次 update 把 v1 归档
    await ac.put("/api/agents/GLM", json={"prompt": "活泼且严谨一点"})
    # 第二次 update 把 v2 归档
    await ac.put("/api/agents/GLM", json={"prompt": "活泼且更详细一些"})

    resp = await ac.get("/api/agents/GLM/history")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    versions = [item["version"] for item in data]
    assert versions == [2, 1]
    # 字段完整性
    sample = data[0]
    assert {"name", "model", "prompt", "version", "archived_at", "archived_reason"} <= set(
        sample.keys()
    )
    assert sample["name"] == "GLM"
    assert sample["archived_reason"] == "upsert"


@pytest.mark.asyncio
async def test_revert_to_v1(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """update v1→v2→v3 后 revert 到 v1 新 current 是 v4 但内容等于 v1"""
    ac, storage, _r = app_client

    # 取 v1 内容(seed 时的 prompt)
    seed = await storage.get_agent("GLM")
    assert seed is not None and seed.version == 1
    v1_model = seed.model
    v1_prompt = seed.prompt

    # 改两次到 v3
    await ac.put("/api/agents/GLM", json={"prompt": "v2 prompt 长一点"})
    await ac.put("/api/agents/GLM", json={"prompt": "v3 prompt 再加点"})
    cur = await storage.get_agent("GLM")
    assert cur is not None and cur.version == 3

    # revert 到 v1
    resp = await ac.post("/api/agents/GLM/revert", json={"target_version": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "GLM"
    assert body["version"] == 4  # v3 归档后 +1
    assert body["reloaded"] is True

    final = await storage.get_agent("GLM")
    assert final is not None
    assert final.version == 4
    assert final.model == v1_model
    assert final.prompt == v1_prompt


@pytest.mark.asyncio
async def test_revert_unknown_version_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """指定的历史 version 不存在 返回 404"""
    ac, _s, _r = app_client
    # 至少触发一次归档保证 history 表非空
    await ac.put("/api/agents/GLM", json={"prompt": "v2 prompt 长一点"})
    resp = await ac.post(
        "/api/agents/GLM/revert", json={"target_version": 999}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revert_unknown_agent_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """agent 不存在 返回 404"""
    ac, _s, _r = app_client
    resp = await ac.post(
        "/api/agents/NoSuchAgent/revert", json={"target_version": 1}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_for_unknown_agent_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """history 接口 agent 不存在 也是 404"""
    ac, _s, _r = app_client
    resp = await ac.get("/api/agents/NoSuchAgent/history")
    assert resp.status_code == 404

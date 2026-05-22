"""provider_profiles CRUD API 路由单元测试 用 mongomock-motor + httpx ASGITransport

被测对象
    - GET    /api/profiles
    - GET    /api/profiles/{name}
    - POST   /api/profiles
    - PUT    /api/profiles/{name}
    - DELETE /api/profiles/{name}

不走完整 lifespan 而是手工挂 storage 与 registry 到 app.state
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
from multichat.routes.profiles import router as profiles_router
from multichat.storage.mongo import MotorMongoStorage


def _settings() -> Settings:
    """构造测试用最小有效 Settings"""
    return Settings(
        key="sk-yaml-seed-tail-7777",
        base_url="https://yaml-seed.example.com/v1",
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
    """fixture 拼装 FastAPI 实例 同时挂 agents 与 profiles 两个 router"""
    settings = _settings()
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "multi_chat_test")
    await storage.ensure_indexes()
    await storage.seed_from_yaml(settings)

    registry = build_registry(settings)
    records = await storage.list_agents()
    profiles = {p.name: p for p in await storage.list_profiles()}
    await registry.initialize(records, profiles)

    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.deep_agents = registry
    app.include_router(agents_router)
    app.include_router(profiles_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, storage, registry

    await storage.close()


# ---------------------------------------------------- 列表 / 详情
@pytest.mark.asyncio
async def test_list_after_seed_returns_default(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """启动后 GET /api/profiles 返回 1 条 名为 '默认'"""
    ac, _s, _r = app_client
    resp = await ac.get("/api/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    p = data[0]
    assert p["name"] == "默认"
    assert p["provider_type"] == "openai_compatible"
    assert p["base_url"] == "https://yaml-seed.example.com/v1"
    # 字段完整性
    assert {"name", "provider_type", "base_url", "api_key", "models", "version", "updated_at"} <= set(p.keys())
    # 模型池非空
    assert len(p["models"]) > 0


@pytest.mark.asyncio
async def test_api_key_masked_in_response(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """返回的 api_key 必须是 mask 形式 不能是明文"""
    ac, _s, _r = app_client
    resp = await ac.get("/api/profiles/默认")
    assert resp.status_code == 200
    body = resp.json()
    raw_key = "sk-yaml-seed-tail-7777"
    assert body["api_key"] != raw_key
    # 末 4 位应该是 7777
    assert body["api_key"].endswith("7777")
    assert "..." in body["api_key"]


@pytest.mark.asyncio
async def test_get_unknown_profile_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    ac, _s, _r = app_client
    resp = await ac.get("/api/profiles/not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------- 创建
@pytest.mark.asyncio
async def test_create_profile(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """POST /api/profiles 新建一个 'claude' profile 应 201 且 api_key 已 mask"""
    ac, storage, _r = app_client
    resp = await ac.post(
        "/api/profiles",
        json={
            "name": "claude",
            "provider_type": "openai_compatible",
            "base_url": "https://claude.example.com/v1",
            "api_key": "sk-claude-tail-9999",
            "models": [{"model_id": "claude-3-5-sonnet", "label": "Claude 3.5 Sonnet"}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "claude"
    # api_key 已 mask
    assert body["api_key"].endswith("9999")
    assert "..." in body["api_key"]
    # DB 实际明文落库 storage 直接读
    p = await storage.get_profile("claude")
    assert p is not None and p.api_key == "sk-claude-tail-9999"


@pytest.mark.asyncio
async def test_create_duplicate_409(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """同名 profile 重复创建 应 409"""
    ac, _s, _r = app_client
    payload = {
        "name": "默认",
        "base_url": "https://other.example.com/v1",
        "api_key": "sk-other-tail-1234",
    }
    resp = await ac.post("/api/profiles", json=payload)
    assert resp.status_code == 409


# ---------------------------------------------------- 更新
@pytest.mark.asyncio
async def test_update_profile_base_url(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """PUT 部分更新 base_url version+1 其它字段保留"""
    ac, storage, _r = app_client
    resp = await ac.put(
        "/api/profiles/默认",
        json={"base_url": "https://changed.example.com/v1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["base_url"] == "https://changed.example.com/v1"
    assert body["version"] == 2
    # api_key 没变 仍然是 mask
    assert body["api_key"].endswith("7777")


@pytest.mark.asyncio
async def test_update_profile_empty_body_400(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """三个字段都不传应 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/profiles/默认", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_unknown_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    ac, _s, _r = app_client
    resp = await ac.put(
        "/api/profiles/not-exist",
        json={"base_url": "https://x.example.com/v1"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------- 删除
@pytest.mark.asyncio
async def test_delete_profile_in_use_409(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """删除仍被 agent 引用的 profile 应 409"""
    ac, _s, _r = app_client
    resp = await ac.delete("/api/profiles/默认")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_orphan_profile_204(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    """删除没人引用的 profile 应 204"""
    ac, _s, _r = app_client
    # 先建一个孤儿 profile
    resp_c = await ac.post(
        "/api/profiles",
        json={
            "name": "orphan",
            "base_url": "https://orphan.example.com/v1",
            "api_key": "sk-orphan-tail-aaaa",
        },
    )
    assert resp_c.status_code == 201

    resp_d = await ac.delete("/api/profiles/orphan")
    assert resp_d.status_code == 204

    # 列表里也确认消失
    resp_l = await ac.get("/api/profiles")
    names = {p["name"] for p in resp_l.json()}
    assert "orphan" not in names


@pytest.mark.asyncio
async def test_delete_unknown_profile_404(
    app_client: tuple[AsyncClient, MotorMongoStorage, object],
) -> None:
    ac, _s, _r = app_client
    resp = await ac.delete("/api/profiles/not-exist")
    assert resp.status_code == 404

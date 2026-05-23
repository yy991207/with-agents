"""模型发现 API 测试

目标是验证后端按 OpenAI 兼容协议代理请求用户填写的 base_url/models。
这个文件不依赖 Mongo fixture，方便先锁住模型发现的接口行为。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import FastAPI
from httpx import AsyncClient as _AsyncClient
from pydantic import BaseModel

from multichat.routes.agents import router as agents_router


def test_discover_models_from_openai_compatible_provider(monkeypatch) -> None:
    """POST /api/models/discover 应按 OpenAI 兼容协议请求 base_url/models"""
    asyncio.run(_run_discover_models_case(monkeypatch))


async def _run_discover_models_case(monkeypatch) -> None:
    """同步测试外壳内部执行异步请求 避免测试环境缺 pytest-asyncio 时收集失败"""
    calls: list[dict[str, Any]] = []

    class FakeProviderClient:
        def __init__(self, **kwargs: Any) -> None:
            calls.append({"init": kwargs})

        async def __aenter__(self) -> "FakeProviderClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
            calls.append({"url": url, "headers": headers})
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "object": "list",
                    "data": [
                        {"id": "qwen-plus", "owned_by": "provider"},
                        {"id": "qwen-max", "owned_by": "provider"},
                    ],
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeProviderClient)
    app = FastAPI()
    app.include_router(agents_router)

    transport = httpx.ASGITransport(app=app)
    async with _AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/api/models/discover",
            json={
                "base_url": "https://provider.example.com/v1/",
                "api_key": "sk-test-123456",
            },
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "models": [
            {"model_id": "qwen-plus", "label": "qwen-plus"},
            {"model_id": "qwen-max", "label": "qwen-max"},
        ]
    }
    assert calls[-1]["url"] == "https://provider.example.com/v1/models"
    assert calls[-1]["headers"]["Authorization"] == "Bearer sk-test-123456"
    assert calls[-1]["headers"]["Accept"] == "application/json"


def test_discover_existing_agent_models_uses_saved_key(monkeypatch) -> None:
    """编辑已有 agent 时 api_key 可不传 后端应使用 storage 中的明文 key"""
    asyncio.run(_run_discover_existing_agent_case(monkeypatch))


async def _run_discover_existing_agent_case(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeProviderClient:
        def __init__(self, **kwargs: Any) -> None:
            calls.append({"init": kwargs})

        async def __aenter__(self) -> "FakeProviderClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
            calls.append({"url": url, "headers": headers})
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"data": [{"id": "stored-key-model"}]},
            )

    class FakeAgent(BaseModel):
        name: str = "agent_a"
        base_url: str = "https://stored.example.com/v1"
        api_key: str = "sk-saved-secret"
        provider_type: str = "openai_compatible"

    class FakeStorage:
        async def get_agent(self, name: str) -> FakeAgent | None:
            return FakeAgent() if name == "agent_a" else None

    monkeypatch.setattr(httpx, "AsyncClient", FakeProviderClient)
    app = FastAPI()
    app.state.storage = FakeStorage()
    app.include_router(agents_router)

    transport = httpx.ASGITransport(app=app)
    async with _AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post("/api/agents/agent_a/models/discover", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "models": [{"model_id": "stored-key-model", "label": "stored-key-model"}]
    }
    assert calls[-1]["url"] == "https://stored.example.com/v1/models"
    assert calls[-1]["headers"]["Authorization"] == "Bearer sk-saved-secret"

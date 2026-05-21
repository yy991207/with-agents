"""路由单元测试 不依赖 M2 真实 task_manager 全部用 AsyncMock 注入

被测对象
    - POST /ask
    - POST /decide
    - POST /cancel
    - POST /retry-think
    - GET  /sse/{task_id}
    - GET  /history/{session_id}
    - GET  /sessions

测试策略
    - 用 AsyncMock 模拟 task_manager 验证路由调用契约
    - 用 mongomock-motor 注入真实 storage 验证 history/sessions 能跑通
    - SSE 用 httpx AsyncClient 流式读 验证 snapshot/event 帧
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from multichat.config import (
    AgentConfig,
    JudgeConfig,
    MongoConfig,
    RuntimeConfig,
    Settings,
)
from multichat.core.models import Round, TaskState
from multichat.routes.ask import router as ask_router
from multichat.routes.cancel import router as cancel_router
from multichat.routes.decide import router as decide_router
from multichat.routes.history import router as history_router
from multichat.routes.retry_think import router as retry_think_router
from multichat.routes.sessions import router as sessions_router
from multichat.routes.stream import router as stream_router
from multichat.storage.mongo import MotorMongoStorage


# ----------------------------------------------------------------- 公共 fixture


def _settings() -> Settings:
    """构造测试用最小 Settings 复用 agents 测试同款"""
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


class _FakeTaskEvent:
    """伪 TaskEvent 仅暴露 to_dict 用于 SSE 测试"""

    def __init__(self, type_: str, data: dict) -> None:
        self.type = type_
        self.data = data

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


class _FakeHub:
    """伪 TaskEventHub 顺序 yield 给定历史 + 增量事件 然后 None 关闭"""

    def __init__(self, history: list[_FakeTaskEvent], live: list[_FakeTaskEvent]) -> None:
        self._history = history
        self._live = live
        self.unsubscribed = False

    async def subscribe(self):
        queue: asyncio.Queue = asyncio.Queue()
        for ev in self._live:
            await queue.put(ev)
        await queue.put(None)  # 表示流关闭
        return list(self._history), queue

    async def unsubscribe(self, q) -> None:  # noqa: ARG002
        self.unsubscribed = True


def _build_app(
    *,
    task_manager: object,
    storage: MotorMongoStorage | None = None,
) -> FastAPI:
    """组装一个最小 FastAPI 实例 仅挂 M3 路由"""
    app = FastAPI()
    # CORS 这里也加上 与 main.py 一致 验证不会拦请求
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.task_manager = task_manager
    if storage is not None:
        app.state.storage = storage
    app.include_router(ask_router)
    app.include_router(decide_router)
    app.include_router(cancel_router)
    app.include_router(retry_think_router)
    app.include_router(stream_router)
    app.include_router(history_router)
    app.include_router(sessions_router)
    return app


@pytest.fixture
async def storage() -> AsyncIterator[MotorMongoStorage]:
    """提供一个干净的 mongomock storage 已 seed"""
    settings = _settings()
    client = AsyncMongoMockClient()
    s = MotorMongoStorage.from_client(client, "multi_chat_test")
    await s.ensure_indexes()
    await s.seed_from_yaml(settings)
    yield s
    await s.close()


# --------------------------------------------------------------------- /ask


@pytest.mark.asyncio
async def test_ask_creates_task_returns_session_and_task_id(
    storage: MotorMongoStorage,
) -> None:
    """POST /ask 期望同时返回 session_id 与 task_id"""
    # 借用真实 storage 先建 round 让 task_manager mock 返回这个 task_id
    session_id = await storage.create_session(title="t")
    task_id = await storage.create_round(session_id, "你好", None)

    tm = MagicMock()
    tm.create_task = AsyncMock(return_value=task_id)
    app = _build_app(task_manager=tm, storage=storage)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post("/ask", json={"user_message": "你好"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"] == task_id
        assert body["session_id"] == session_id

    tm.create_task.assert_awaited_once_with(None, "你好")


@pytest.mark.asyncio
async def test_ask_round_missing_returns_500(storage: MotorMongoStorage) -> None:
    """task_manager 返回了不存在的 task_id 应 500"""
    tm = MagicMock()
    tm.create_task = AsyncMock(return_value="nonexistent-task")
    app = _build_app(task_manager=tm, storage=storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post("/ask", json={"user_message": "你好"})
        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_ask_validates_empty_message(storage: MotorMongoStorage) -> None:
    """user_message 为空 应被 pydantic 拦下 422"""
    tm = MagicMock()
    tm.create_task = AsyncMock()
    app = _build_app(task_manager=tm, storage=storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post("/ask", json={"user_message": ""})
        assert resp.status_code == 422


# --------------------------------------------------------------------- /decide


@pytest.mark.asyncio
async def test_decide_204() -> None:
    """正常 decide 返回 204"""
    tm = MagicMock()
    tm.submit_decision = AsyncMock()
    app = _build_app(task_manager=tm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/decide", json={"task_id": "t1", "choice": "GLM"}
        )
        assert resp.status_code == 204
    tm.submit_decision.assert_awaited_once_with("t1", "GLM")


@pytest.mark.asyncio
async def test_decide_unknown_task_409() -> None:
    """task_manager 抛 KeyError 路由 409"""
    tm = MagicMock()
    tm.submit_decision = AsyncMock(side_effect=KeyError("t1"))
    app = _build_app(task_manager=tm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/decide", json={"task_id": "t1", "choice": "GLM"}
        )
        assert resp.status_code == 409


# --------------------------------------------------------------------- /cancel


@pytest.mark.asyncio
async def test_cancel_204() -> None:
    """正常 cancel 返回 204"""
    tm = MagicMock()
    tm.cancel_task = AsyncMock()
    app = _build_app(task_manager=tm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/cancel", json={"task_id": "t1", "scope": "global"}
        )
        assert resp.status_code == 204
    tm.cancel_task.assert_awaited_once_with("t1", "global")


# --------------------------------------------------------------------- /retry-think


@pytest.mark.asyncio
async def test_retry_think_returns_501() -> None:
    """task_manager 抛 NotImplementedError 路由 501"""
    tm = MagicMock()
    tm.retry_think = AsyncMock(side_effect=NotImplementedError())
    app = _build_app(task_manager=tm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/retry-think", json={"task_id": "t1", "agent": "GLM"}
        )
        assert resp.status_code == 501


@pytest.mark.asyncio
async def test_retry_think_204_when_supported() -> None:
    """若 task_manager 实装支持 应返回 204"""
    tm = MagicMock()
    tm.retry_think = AsyncMock()
    app = _build_app(task_manager=tm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/retry-think", json={"task_id": "t1", "agent": "GLM"}
        )
        assert resp.status_code == 204


# --------------------------------------------------------------------- /history


@pytest.mark.asyncio
async def test_history_session_not_found_404(storage: MotorMongoStorage) -> None:
    """session 不存在 404"""
    tm = MagicMock()
    app = _build_app(task_manager=tm, storage=storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/history/no-such-session")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_returns_session_and_rounds(storage: MotorMongoStorage) -> None:
    """正常路径 返回 session + rounds"""
    sid = await storage.create_session(title="hello")
    tid = await storage.create_round(sid, "Q1", None)
    await storage.update_round_state(tid, TaskState.DONE)

    tm = MagicMock()
    app = _build_app(task_manager=tm, storage=storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get(f"/history/{sid}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["session"]["session_id"] == sid
        assert isinstance(body["rounds"], list)
        assert len(body["rounds"]) == 1
        r0: Round = Round.model_validate(body["rounds"][0])
        assert r0.task_id == tid
        assert r0.question == "Q1"


# --------------------------------------------------------------------- /sessions


@pytest.mark.asyncio
async def test_sessions_list(storage: MotorMongoStorage) -> None:
    """sessions 列表能正确返回 SessionMeta"""
    s1 = await storage.create_session(title="s1")
    s2 = await storage.create_session(title="s2")

    tm = MagicMock()
    app = _build_app(task_manager=tm, storage=storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        ids = {item["session_id"] for item in data}
        assert {s1, s2} <= ids


# --------------------------------------------------------------------- /sse


@pytest.mark.asyncio
async def test_sse_unknown_task_404() -> None:
    """get_hub 返回 None 时 404"""
    tm = MagicMock()
    tm.get_hub = MagicMock(return_value=None)
    app = _build_app(task_manager=tm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/sse/unknown-task")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sse_basic_stream() -> None:
    """SSE 流应先吐 snapshot 帧 再吐增量事件 最终关闭"""
    history = [_FakeTaskEvent("status", {"state": "thinking"})]
    live = [
        _FakeTaskEvent("think_chunk", {"agent": "GLM", "text": "你"}),
        _FakeTaskEvent("think_done", {"agent": "GLM"}),
    ]
    hub = _FakeHub(history=history, live=live)
    tm = MagicMock()
    tm.get_hub = MagicMock(return_value=hub)
    app = _build_app(task_manager=tm)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # 用 stream 模式 流式读
        async with ac.stream("GET", "/sse/t1") as resp:
            assert resp.status_code == 200
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct, ct

            # 累积所有 chunk 然后按 SSE 帧分割解析
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk

    # 解析 SSE 帧 分隔符是 \r\n\r\n 或 \n\n
    raw_frames = [f for f in buffer.replace("\r\n", "\n").split("\n\n") if f.strip()]
    parsed: list[tuple[str, dict]] = []
    for frame in raw_frames:
        ev_name = None
        data_lines: list[str] = []
        for line in frame.split("\n"):
            if line.startswith(":"):
                # ping 注释帧 跳过
                continue
            if line.startswith("event:"):
                ev_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        if ev_name is None:
            continue
        try:
            payload = json.loads("\n".join(data_lines)) if data_lines else {}
        except json.JSONDecodeError:
            payload = {}
        parsed.append((ev_name, payload))

    # 第一帧应是 snapshot 含历史事件
    assert parsed[0][0] == "snapshot"
    assert "events" in parsed[0][1]
    assert parsed[0][1]["events"][0]["type"] == "status"

    # 后续是增量帧 顺序应一致
    assert parsed[1][0] == "think_chunk"
    assert parsed[1][1] == {"agent": "GLM", "text": "你"}
    assert parsed[2][0] == "think_done"
    assert parsed[2][1] == {"agent": "GLM"}

    # 流结束后 hub 应已被 unsubscribe
    assert hub.unsubscribed is True

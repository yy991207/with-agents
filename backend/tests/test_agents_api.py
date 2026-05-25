"""agents CRUD API 路由单元测试 用 mongomock-motor + httpx ASGITransport

被测对象
    - GET    /api/agents
    - POST   /api/agents
    - PUT    /api/agents/{name}
    - DELETE /api/agents/{name}
    - PUT    /api/judge
    - GET    /api/agents/{name}/history
    - POST   /api/agents/{name}/revert

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
        key="sk-test-tail-1234",
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
async def test_list_agents_after_seed(app_client) -> None:
    """seed 后 GET /api/agents 应返回 4 条 + judge_target=GLM"""
    ac, _storage, _reg = app_client
    resp = await ac.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    names = {a["name"] for a in data["agents"]}
    assert names == {"DeepSeek", "GLM", "Kimi", "Qwen"}
    # 默认 judge 指针来自 yaml.judge.agent
    assert data["judge_target"] == "GLM"
    # 字段完整性 完整数字员工字段
    sample = data["agents"][0]
    assert {
        "name",
        "display_name",
        "provider_type",
        "base_url",
        "api_key",
        "model",
        "available_models",
        "prompt",
        "version",
        "updated_at",
    } <= set(sample.keys())
    # version 都是 1 因 seed 初始化
    assert all(a["version"] == 1 for a in data["agents"])
    # display_name 默认 == name
    assert all(a["display_name"] == a["name"] for a in data["agents"])
    # api_key 已 mask
    for a in data["agents"]:
        assert "..." in a["api_key"]
        assert a["api_key"].endswith("1234")  # _settings key 末 4 位


@pytest.mark.asyncio
async def test_api_key_masked(app_client) -> None:
    """GET 时 api_key 必须 mask  原 key 在 storage 内仍是明文"""
    ac, storage, _reg = app_client
    resp = await ac.get("/api/agents")
    data = resp.json()
    for a in data["agents"]:
        assert a["api_key"] != "sk-test-tail-1234"
        assert a["api_key"].startswith("sk-")
    # 原文仍是明文存在 storage
    rec = await storage.get_agent("GLM")
    assert rec is not None and rec.api_key == "sk-test-tail-1234"


@pytest.mark.asyncio
async def test_create_agent_auto_name(app_client) -> None:
    """POST /api/agents 不带 name  返回的 name 形如 agent_<8位hex>"""
    ac, storage, registry = app_client
    payload = {
        "display_name": "我的新员工",
        "base_url": "https://api.foo.example.com/v1",
        "api_key": "sk-newkey-tail-9876",
        "model": "gpt-foo",
        "prompt": "你是新加入的员工",
        "available_models": [{"model_id": "gpt-foo", "label": "Foo"}],
    }
    resp = await ac.post("/api/agents", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"].startswith("agent_")
    assert len(data["name"]) == len("agent_") + 8
    assert data["display_name"] == "我的新员工"
    assert data["api_key"] != "sk-newkey-tail-9876"  # 已 mask
    assert data["api_key"].endswith("9876")
    # storage 中确实保存了
    rec = await storage.get_agent(data["name"])
    assert rec is not None and rec.display_name == "我的新员工"
    # registry 也已注册
    assert data["name"] in registry.names()


@pytest.mark.asyncio
async def test_create_agent_short_field_validation_422(app_client) -> None:
    """字段太短走 pydantic 422"""
    ac, _s, _r = app_client
    resp = await ac.post(
        "/api/agents",
        json={
            "display_name": "",  # 必传 长度 1
            "base_url": "https://x.example.com/v1",
            "api_key": "sk-x",  # 长度 4 OK
            "model": "m",
            "prompt": "p",  # 长度 < 5 失败
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_agent_204(app_client) -> None:
    """删除非 judge 的 agent  204 后 list 不再有它"""
    ac, storage, registry = app_client
    resp = await ac.delete("/api/agents/Kimi")
    assert resp.status_code == 204
    rec = await storage.get_agent("Kimi")
    assert rec is None
    assert "Kimi" not in registry.names()


@pytest.mark.asyncio
async def test_delete_agent_judge_target_409(app_client) -> None:
    """删 judge target 应 409"""
    ac, storage, _reg = app_client
    # GLM 是默认 judge
    resp = await ac.delete("/api/agents/GLM")
    assert resp.status_code == 409
    # storage 中 GLM 仍然存在
    assert (await storage.get_agent("GLM")) is not None


@pytest.mark.asyncio
async def test_delete_agent_404(app_client) -> None:
    """删除不存在的 agent 应 404"""
    ac, _s, _r = app_client
    resp = await ac.delete("/api/agents/no-such-agent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_display_name(app_client) -> None:
    """PUT /api/agents/GLM 改 display_name 不影响 name 与历史引用"""
    ac, storage, _reg = app_client
    resp = await ac.put(
        "/api/agents/GLM",
        json={"display_name": "可爱小G"},
    )
    assert resp.status_code == 200, resp.text
    rec = await storage.get_agent("GLM")
    assert rec is not None
    assert rec.name == "GLM"  # name 不变
    assert rec.display_name == "可爱小G"


@pytest.mark.asyncio
async def test_update_base_url_and_key(app_client) -> None:
    """PUT 改 base_url + api_key 应都生效"""
    ac, storage, _reg = app_client
    resp = await ac.put(
        "/api/agents/GLM",
        json={
            "base_url": "https://new.example.com/v1",
            "api_key": "sk-newer-tail-5566",
        },
    )
    assert resp.status_code == 200, resp.text
    rec = await storage.get_agent("GLM")
    assert rec is not None
    assert rec.base_url == "https://new.example.com/v1"
    assert rec.api_key == "sk-newer-tail-5566"
    assert rec.version == 2


@pytest.mark.asyncio
async def test_update_agent_prompt(app_client) -> None:
    """PUT /api/agents/GLM 改 prompt version 升到 2 且 registry 实例换新"""
    ac, storage, registry = app_client

    old_think = registry.get("GLM", "think")
    old_reply = registry.get("GLM", "reply")

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
    new_think = registry.get("GLM", "think")
    new_reply = registry.get("GLM", "reply")
    assert new_think is not old_think
    assert new_reply is not old_reply

    # DB 内容也更新
    rec = await storage.get_agent("GLM")
    assert rec is not None and rec.prompt == "活泼但更严谨一点表达"


@pytest.mark.asyncio
async def test_update_agent_unknown_404(app_client) -> None:
    """改不存在的 agent 应 404"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/agents/Unknown", json={"prompt": "你好世界你好"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_agent_empty_body_400(app_client) -> None:
    """body 完全为空 返回 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/agents/GLM", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_agent_short_prompt_400(app_client) -> None:
    """prompt 长度不足 5 字 返回 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/agents/GLM", json={"prompt": "ab"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_agent_version_conflict_409(app_client) -> None:
    """expected_version 与服务端不一致 返回 409"""
    ac, _s, _r = app_client
    resp = await ac.put(
        "/api/agents/GLM",
        json={"prompt": "活泼但更严谨一点", "expected_version": 999},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_judge(app_client) -> None:
    """PUT /api/judge target=Kimi 后 GET /api/agents 的 judge_target=Kimi"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/judge", json={"target": "Kimi"})
    assert resp.status_code == 204

    resp2 = await ac.get("/api/agents")
    assert resp2.status_code == 200
    assert resp2.json()["judge_target"] == "Kimi"


@pytest.mark.asyncio
async def test_update_judge_unknown_400(app_client) -> None:
    """target 不在已有 agent 中 返回 400"""
    ac, _s, _r = app_client
    resp = await ac.put("/api/judge", json={"target": "Unknown"})
    assert resp.status_code == 400


# ------------------------------------------------------- 配置历史/回滚
@pytest.mark.asyncio
async def test_list_agent_history_returns_versions(app_client) -> None:
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
    # 字段完整性 含 display_name/base_url/api_key/model/prompt
    sample = data[0]
    assert {
        "name",
        "display_name",
        "base_url",
        "api_key",
        "model",
        "prompt",
        "version",
        "archived_at",
        "archived_reason",
    } <= set(sample.keys())
    assert sample["name"] == "GLM"
    assert sample["archived_reason"] == "upsert"
    # api_key 已 mask
    assert "..." in sample["api_key"]


@pytest.mark.asyncio
async def test_revert_to_v1(app_client) -> None:
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
async def test_revert_unknown_version_404(app_client) -> None:
    """指定的历史 version 不存在 返回 404"""
    ac, _s, _r = app_client
    # 至少触发一次归档保证 history 表非空
    await ac.put("/api/agents/GLM", json={"prompt": "v2 prompt 长一点"})
    resp = await ac.post(
        "/api/agents/GLM/revert", json={"target_version": 999}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revert_unknown_agent_404(app_client) -> None:
    """agent 不存在 返回 404"""
    ac, _s, _r = app_client
    resp = await ac.post(
        "/api/agents/NoSuchAgent/revert", json={"target_version": 1}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_for_unknown_agent_404(app_client) -> None:
    """history 接口 agent 不存在 也是 404"""
    ac, _s, _r = app_client
    resp = await ac.get("/api/agents/NoSuchAgent/history")
    assert resp.status_code == 404


# ------------------------------------------------------- 头像上传 / 删除
# 1x1 透明 PNG 最小完整文件 用于测试上传  实际渲染浏览器能识别
_SMALL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_upload_avatar_ok(app_client) -> None:
    """上传 PNG 后 agent.avatar_data_url 形如 data:image/png;base64,xxx
    返回的 AgentView 也应该带这个字段
    持久化进 mongo 后再 GET /api/agents 仍可看到
    """
    ac, storage, _r = app_client
    files = {"file": ("avatar.png", _SMALL_PNG, "image/png")}
    resp = await ac.post("/api/agents/GLM/avatar", files=files)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data.get("avatar_data_url"), str)
    assert data["avatar_data_url"].startswith("data:image/png;base64,")

    # 存储层持久化
    rec = await storage.get_agent("GLM")
    assert rec is not None
    assert rec.avatar_data_url == data["avatar_data_url"]

    # 列表接口也应携带
    list_resp = await ac.get("/api/agents")
    glm = next(a for a in list_resp.json()["agents"] if a["name"] == "GLM")
    assert glm["avatar_data_url"] == data["avatar_data_url"]


@pytest.mark.asyncio
async def test_upload_avatar_too_large_413(app_client) -> None:
    """超过 2MB 必须 413  防止把巨图塞进 mongo 把 doc 撑爆"""
    ac, _s, _r = app_client
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 100)
    files = {"file": ("big.png", big, "image/png")}
    resp = await ac.post("/api/agents/GLM/avatar", files=files)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_upload_avatar_unsupported_mime_415(app_client) -> None:
    """非 png/jpeg/webp/gif 应 415"""
    ac, _s, _r = app_client
    files = {"file": ("bad.txt", b"not an image at all", "text/plain")}
    resp = await ac.post("/api/agents/GLM/avatar", files=files)
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_avatar_unknown_agent_404(app_client) -> None:
    """目标 agent 不存在  返回 404"""
    ac, _s, _r = app_client
    files = {"file": ("a.png", _SMALL_PNG, "image/png")}
    resp = await ac.post("/api/agents/NoSuchAgent/avatar", files=files)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_avatar_clears_field(app_client) -> None:
    """先上传后删除  字段回到 None"""
    ac, storage, _r = app_client
    files = {"file": ("a.png", _SMALL_PNG, "image/png")}
    up = await ac.post("/api/agents/GLM/avatar", files=files)
    assert up.status_code == 200

    resp = await ac.delete("/api/agents/GLM/avatar")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avatar_data_url"] is None

    rec = await storage.get_agent("GLM")
    assert rec is not None
    assert rec.avatar_data_url is None


@pytest.mark.asyncio
async def test_delete_avatar_unknown_agent_404(app_client) -> None:
    """删除不存在 agent 的头像  404"""
    ac, _s, _r = app_client
    resp = await ac.delete("/api/agents/NoSuchAgent/avatar")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_avatar_change_does_not_bump_version(app_client) -> None:
    """头像变更不应影响 version  agent_history 不应新增归档
    头像是展示数据  不是 agent 配置变更
    """
    ac, storage, _r = app_client
    before = await storage.get_agent("GLM")
    assert before is not None
    before_version = before.version
    before_history = await storage.list_agent_history("GLM", limit=10)
    before_history_count = len(before_history)

    # 上传 + 删除各一次
    await ac.post(
        "/api/agents/GLM/avatar",
        files={"file": ("a.png", _SMALL_PNG, "image/png")},
    )
    await ac.delete("/api/agents/GLM/avatar")

    after = await storage.get_agent("GLM")
    assert after is not None
    assert after.version == before_version, "头像变更不应让 version +1"

    after_history = await storage.list_agent_history("GLM", limit=10)
    assert len(after_history) == before_history_count, "头像变更不应进 agent_history"

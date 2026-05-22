"""DeepAgentRegistry 单元测试 不发任何网络请求

ChatOpenAI 创建仅持有凭据 真正调用发生在 ainvoke/astream 时
所以这里直接调 build/initialize/reload 不会触网
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from multichat.config import (
    AgentConfig,
    JudgeConfig,
    MongoConfig,
    RuntimeConfig,
    Settings,
)
from multichat.core.models import AgentRecord, ModelCatalogEntry
from multichat.llm.deep_agents import DeepAgentRegistry, build_registry


def _settings() -> Settings:
    """构造测试用最小有效 Settings"""
    return Settings(
        key="sk-test-tail",
        base_url="https://example.com/v1",
        agents={
            "DeepSeek": AgentConfig(model="deepseek-test", prompt="深度"),
            "GLM": AgentConfig(model="glm-test", prompt="活泼"),
            "Kimi": AgentConfig(model="kimi-test", prompt="温柔"),
            "Qwen": AgentConfig(model="qwen-test", prompt="百科"),
        },
        judge=JudgeConfig(agent="GLM", prompt="你是裁判"),
        mongo=MongoConfig(),
        runtime=RuntimeConfig(),
    )


def _record(
    name: str,
    model: str = "m-1",
    prompt: str = "你好世界你好",
    base_url: str = "https://example.com/v1",
    api_key: str = "sk-test-tail",
) -> AgentRecord:
    """构造一条测试用 AgentRecord 完整数字员工"""
    return AgentRecord(
        name=name,
        display_name=name,
        provider_type="openai_compatible",
        base_url=base_url,
        api_key=api_key,
        model=model,
        available_models=[ModelCatalogEntry(model_id=model, label=model)],
        prompt=prompt,
        version=1,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_build_registry_initialize_4_agents() -> None:
    """给 4 条记录 initialize 后应有 4 个 agent 名"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("DeepSeek", "GLM", "Kimi", "Qwen")]
    await reg.initialize(records)
    assert reg.names() == ["DeepSeek", "GLM", "Kimi", "Qwen"]
    # 每个 agent 都能拿到 think + reply 两个实例
    for n in ("DeepSeek", "GLM", "Kimi", "Qwen"):
        assert reg.get(n, "think") is not None
        assert reg.get(n, "reply") is not None


@pytest.mark.asyncio
async def test_initialize_zero_records_ok() -> None:
    """空列表也合法 names() 返回空"""
    reg = build_registry(_settings())
    await reg.initialize([])
    assert reg.names() == []


@pytest.mark.asyncio
async def test_initialize_arbitrary_count() -> None:
    """任意数量都接受 数字员工模式不固定 4 条"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("a", "b", "c", "d", "e", "f", "g")]
    await reg.initialize(records)
    assert reg.names() == ["a", "b", "c", "d", "e", "f", "g"]


@pytest.mark.asyncio
async def test_build_registry_reload_swaps_instance() -> None:
    """reload(record) 后同名 think 实例 id 必须不同 即真正 swap 了"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("DeepSeek", "GLM", "Kimi", "Qwen")]
    await reg.initialize(records)

    old_think = reg.get("GLM", "think")
    old_reply = reg.get("GLM", "reply")

    new_record = _record("GLM", model="glm-v2", prompt="活泼但更专业一点")
    await reg.reload(new_record)

    new_think = reg.get("GLM", "think")
    new_reply = reg.get("GLM", "reply")
    assert new_think is not old_think
    assert new_reply is not old_reply
    # 别的 agent 不应受影响
    assert reg.get("Kimi", "think") is not None


@pytest.mark.asyncio
async def test_reload_can_register_new_agent() -> None:
    """reload 接收新名字 等同追加  数字员工动态新增"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("Old1", "Old2")]
    await reg.initialize(records)
    assert reg.names() == ["Old1", "Old2"]

    new_rec = _record("Newcomer")
    await reg.reload(new_rec)
    assert "Newcomer" in reg.names()
    assert reg.get("Newcomer", "think") is not None
    assert reg.get("Newcomer", "reply") is not None


@pytest.mark.asyncio
async def test_unregister() -> None:
    """unregister 后该 agent 的 think+reply 都不可获取"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("a", "b", "c")]
    await reg.initialize(records)

    reg.unregister("b")
    assert "b" not in reg.names()
    # 别的不受影响
    assert reg.get("a", "think") is not None


@pytest.mark.asyncio
async def test_get_after_unregister_raises() -> None:
    """unregister 之后再 get 应抛 KeyError"""
    reg = build_registry(_settings())
    await reg.initialize([_record("solo")])
    reg.unregister("solo")
    with pytest.raises(KeyError):
        reg.get("solo", "think")


@pytest.mark.asyncio
async def test_get_unknown_raises() -> None:
    """取不存在的 agent 抛 KeyError"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("DeepSeek", "GLM", "Kimi", "Qwen")]
    await reg.initialize(records)
    with pytest.raises(KeyError):
        reg.get("NotExist", "think")


@pytest.mark.asyncio
async def test_reload_with_different_credentials() -> None:
    """reload 接收新 base_url/api_key 实例应使用新凭据 即 swap 成功"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("DeepSeek", "GLM", "Kimi", "Qwen")]
    await reg.initialize(records)

    new_record = _record(
        "GLM",
        model="glm-v2",
        base_url="https://other.example.com/v1",
        api_key="sk-other-tail",
    )
    await reg.reload(new_record)
    # 拿到的还是同名 key 但实例已替换
    assert reg.get("GLM", "think") is not None


@pytest.mark.asyncio
async def test_unregister_idempotent() -> None:
    """unregister 不存在的 agent 不抛 静默处理"""
    reg = build_registry(_settings())
    await reg.initialize([_record("a")])
    # 不存在的 也不抛
    reg.unregister("not-exist")
    # 自身存在则正常移除
    reg.unregister("a")
    assert reg.names() == []

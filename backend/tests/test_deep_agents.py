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
from multichat.core.models import AgentRecord
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


def _record(name: str, model: str = "m-1", prompt: str = "你好世界你好") -> AgentRecord:
    """构造一条测试用 AgentRecord"""
    return AgentRecord(
        name=name,
        model=model,
        prompt=prompt,
        kind="agent",
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
async def test_build_registry_reload_swaps_instance() -> None:
    """reload 后同名 think 实例 id 必须不同 即真正 swap 了"""
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
async def test_get_unknown_raises() -> None:
    """取不存在的 agent 抛 KeyError"""
    reg = build_registry(_settings())
    records = [_record(n) for n in ("DeepSeek", "GLM", "Kimi", "Qwen")]
    await reg.initialize(records)
    with pytest.raises(KeyError):
        reg.get("NotExist", "think")


@pytest.mark.asyncio
async def test_initialize_wrong_count_raises() -> None:
    """records 不是 4 条直接抛 ValueError 不允许半启动"""
    reg = DeepAgentRegistry(_settings())
    with pytest.raises(ValueError):
        await reg.initialize([_record("GLM"), _record("Kimi"), _record("Qwen")])

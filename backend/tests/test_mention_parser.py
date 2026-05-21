"""mention 解析单元测试 覆盖大小写不敏感 边界以及 valid_agents 过滤"""

from __future__ import annotations

from multichat.core.mention_parser import parse_single_mention


def test_no_mention_returns_none() -> None:
    """普通消息不含 @ 返回 None"""
    assert parse_single_mention("你好世界", valid_agents=["GLM"]) is None


def test_basic_mention_hit() -> None:
    """@GLM 在合法 agent 列表中 应命中并返回标准名"""
    assert parse_single_mention("@GLM 帮我看下", valid_agents=["DeepSeek", "GLM"]) == "GLM"


def test_case_insensitive_match() -> None:
    """@glm 大小写不敏感 仍命中标准名 GLM"""
    assert parse_single_mention("@glm 你好", valid_agents=["GLM"]) == "GLM"


def test_unknown_mention_returns_none() -> None:
    """@Other 不在 valid_agents 中 返回 None"""
    assert parse_single_mention("@Other 嗨", valid_agents=["GLM"]) is None


def test_first_mention_wins() -> None:
    """消息含多个 @ 仅第一个有效 @ 生效"""
    assert (
        parse_single_mention(
            "@GLM 你好顺便问 @Kimi 有没空", valid_agents=["GLM", "Kimi"]
        )
        == "GLM"
    )


def test_empty_text_returns_none() -> None:
    """空文本直接 None"""
    assert parse_single_mention("", valid_agents=["GLM"]) is None


def test_no_valid_agents_returns_none() -> None:
    """valid_agents 为空集合 任何 @ 都不命中"""
    assert parse_single_mention("@GLM 你好", valid_agents=[]) is None
    assert parse_single_mention("@GLM 你好", valid_agents=None) is None


def test_mention_followed_by_punct() -> None:
    """@GLM, 后面带逗号 应正确切分出 GLM"""
    assert parse_single_mention("@GLM, 看一下", valid_agents=["GLM"]) == "GLM"

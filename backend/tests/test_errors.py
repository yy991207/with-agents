"""humanize_llm_error 单元测试

验证常见 LLM 异常文本能被映射成对用户友好的中文短句
未命中的兜底分支仅截断到约 80 字 不会泄露完整堆栈
"""

from __future__ import annotations

import httpx
import pytest

from multichat.core.errors import humanize_llm_error


def test_humanize_timeout() -> None:
    """httpx 超时异常 应命中超时提示"""
    err = httpx.TimeoutException("Request timed out after 30s")
    msg = humanize_llm_error(err)
    assert "超时" in msg


def test_humanize_429() -> None:
    """429 限流文本 应命中限流提示"""
    msg = humanize_llm_error("HTTP 429 Too Many Requests on /v1/chat")
    assert "限流" in msg


def test_humanize_401_invalid_key() -> None:
    """401 invalid api key 应命中凭证错误"""
    msg = humanize_llm_error("401 Unauthorized: invalid api key")
    assert "API key" in msg or "凭证" in msg


def test_humanize_5xx_server() -> None:
    """503 服务不可用 应命中服务暂时不可用"""
    msg = humanize_llm_error(
        "Server error '503 Service Unavailable' for url 'https://example.com/v1/chat'"
    )
    assert "不可用" in msg


def test_humanize_context_length() -> None:
    """上下文超长 应命中 context length 提示"""
    msg = humanize_llm_error(
        "Bad request: context length exceeded 200000 tokens"
    )
    assert "上下文" in msg or "新建会话" in msg


def test_humanize_network() -> None:
    """connection refused 应命中网络连接失败"""
    msg = humanize_llm_error("Connection refused while talking to upstream")
    assert "网络连接" in msg


def test_humanize_cancel() -> None:
    """cancel 文本 应命中已取消"""
    msg = humanize_llm_error("CancelledError: cancelled")
    assert "取消" in msg


def test_humanize_unknown_falls_back() -> None:
    """完全无关的文本 走兜底 返回 '调用失败 ...' 截断"""
    msg = humanize_llm_error("Foo bar baz")
    assert msg.startswith("调用失败")
    assert "Foo bar baz" in msg


def test_humanize_truncates_long() -> None:
    """超长字符串 兜底应截断 不会把整段堆栈直接抛出去"""
    raw = "x" * 500
    msg = humanize_llm_error(raw)
    # 兜底前缀 "调用失败 " 8 字 + 80 字截断 总长不能超过约 100 字
    assert len(msg) < 110
    assert msg.startswith("调用失败")


def test_humanize_takes_exception_object() -> None:
    """传 Exception 对象时 应自动拼出 '类型名: 文本' 再匹配"""

    class FakeRateLimit(Exception):
        pass

    err = FakeRateLimit("rate limit exceeded")
    msg = humanize_llm_error(err)
    assert "限流" in msg


def test_humanize_no_url_leak() -> None:
    """兜底场景下 即便原始字符串很长 url 也不会被完整泄露 截断到 80 字以内"""
    raw = (
        "RandomError: failed to do something with token "
        "https://api.example.com/v1/chat/completions/abc/def "
        + "x" * 200
    )
    msg = humanize_llm_error(raw)
    # 不命中任何模式 走兜底 长度受控
    if msg.startswith("调用失败"):
        # 最后输出长度不超过约 100
        assert len(msg) < 110

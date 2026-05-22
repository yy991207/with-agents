"""共享 tool 集合单元测试

策略
    - 不发任何真实网络请求 全部用 httpx.MockTransport 拦截
    - tool 用 @tool 装饰过 直接调用 tool.ainvoke({...}) 触发底层协程
    - SSRF 防护 用例覆盖各类内网地址表达
    - web_search HTML 解析容错 用例覆盖空结果
"""

from __future__ import annotations

import httpx
import pytest

from multichat.llm import tools as tools_mod
from multichat.llm.tools import (
    current_time,
    get_shared_tools,
    http_get,
    web_search,
)


# ----------------------------------------------------------- current_time
@pytest.mark.asyncio
async def test_current_time_format() -> None:
    """默认 UTC+8 输出格式正确 含时区后缀"""
    out = await current_time.ainvoke({})
    # 形如 "2026-05-21 19:30:00 (UTC+8)"
    assert "(UTC+8)" in out
    # 头部应是日期时间形态 YYYY-MM-DD HH:MM:SS
    assert len(out.split(" ")) >= 2
    date_part = out.split(" ")[0]
    assert len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-"


@pytest.mark.asyncio
async def test_current_time_negative_offset() -> None:
    """负时区偏移格式正确"""
    out = await current_time.ainvoke({"tz_offset_hours": -5})
    assert "(UTC-5)" in out


# ----------------------------------------------------------- http_get 防御
@pytest.mark.asyncio
async def test_http_get_invalid_scheme() -> None:
    """非 http/https 协议立即拒绝"""
    out = await http_get.ainvoke({"url": "ftp://example.com/file"})
    assert out.startswith("[错误]")
    assert "http 或 https" in out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/foo",
        "http://127.0.0.1/foo",
        "http://10.0.0.1/foo",
        "http://192.168.1.1/foo",
        "http://172.16.0.1/foo",
        "http://169.254.169.254/meta",
    ],
)
async def test_http_get_internal_blocked(url: str) -> None:
    """内网/回环/链路本地地址全部拒绝"""
    out = await http_get.ainvoke({"url": url})
    assert out.startswith("[错误]")
    assert "内网" in out


# ----------------------------------------------------------- http_get 截断
@pytest.mark.asyncio
async def test_http_get_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    """大于 100KB 响应应被截断 末尾带标记"""
    big_body = "x" * 200_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big_body)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        # 强制注入 mock transport 不拨号真实网络
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", fake_async_client)

    out = await http_get.ainvoke({"url": "https://example.com/big"})
    # 100_000 + 截断标记长度
    assert out.endswith("...(已截断)")
    # 截断后正文长度等于上限
    body = out.removesuffix("...(已截断)").rstrip("\n")
    assert len(body) == 100_000


@pytest.mark.asyncio
async def test_http_get_normal_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常 200 响应直接透传 不截断"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello world")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", fake_async_client)

    out = await http_get.ainvoke({"url": "https://example.com/hi"})
    assert out == "hello world"


@pytest.mark.asyncio
async def test_http_get_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 2xx 响应应返回错误字符串而非抛异常"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", fake_async_client)

    out = await http_get.ainvoke({"url": "https://example.com/err"})
    assert out.startswith("[错误]")
    assert "500" in out


# ----------------------------------------------------------- web_search
@pytest.mark.asyncio
async def test_web_search_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 HTML 应返回未找到结果 不抛异常"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>nothing</body></html>")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", fake_async_client)

    out = await web_search.ainvoke({"query": "abcxyz"})
    assert out.startswith("[未找到结果]")


@pytest.mark.asyncio
async def test_web_search_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 DDG 返回的 HTML 结构 应抽出标题/链接/摘要"""
    fake_html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Title A</a>
      <a class="result__snippet" href="...">Snippet A about something</a>
    </div>
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Title B</a>
      <a class="result__snippet" href="...">Snippet B</a>
    </div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=fake_html)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", fake_async_client)

    out = await web_search.ainvoke({"query": "test", "max_results": 2})
    lines = out.split("\n")
    assert len(lines) == 2
    assert "Title A" in lines[0]
    assert "https://example.com/a" in lines[0]
    assert "Snippet A" in lines[0]
    assert "Title B" in lines[1]


@pytest.mark.asyncio
async def test_web_search_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """搜索接口直接抛异常 应返回错误字符串而非穿透"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed")

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", fake_async_client)

    out = await web_search.ainvoke({"query": "test"})
    assert out.startswith("[错误]")


# ----------------------------------------------------------- 集合导出
def test_get_shared_tools_returns_3() -> None:
    """共享 tool 列表恰 3 个 每个都带 langchain tool 名字属性"""
    tools = get_shared_tools()
    assert len(tools) == 3
    names = {getattr(t, "name", None) for t in tools}
    assert names == {"current_time", "http_get", "web_search"}

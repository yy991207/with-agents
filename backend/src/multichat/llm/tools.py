"""reply 阶段共享 tool 集合 deep_agent 调用时注入

设计要点
    - tool 是无状态函数 通过 langchain 的 RunnableConfig 注入 task_id agent_name 用于日志隔离
    - tool 之间不共享可变全局状态 涉及 IO 时 client 在函数内 with 创建
    - 安全 http_get/web_search 屏蔽内网 IP 防 SSRF
    - 谁创建谁使用 httpx.AsyncClient 在每次调用 with 内创建 与当前 loop 绑定 不跨线程复用
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import structlog
from langchain_core.tools import tool

_logger = structlog.get_logger(__name__)

# 默认值 单位与含义见各 tool docstring
_HTTP_TIMEOUT_S = 10.0
_HTTP_MAX_BYTES = 100_000
_USER_AGENT = "multichat-bot/0.1"


def _is_internal_url(url: str) -> bool:
    """简单 SSRF 防护 拒绝常见内网/回环/链路本地地址

    判定逻辑
        1 域名直拒列表 localhost / 元数据接口
        2 主机串能 parse 成 IP 时按 IP 类型判断 私有/回环/链路本地都拒
        3 普通域名留给后续 DNS 解析时再处理 这里不做主动 DNS
    """
    try:
        host = urlparse(url).hostname or ""
        # 域名直拒列表
        if host in {"localhost", "metadata.google.internal", "169.254.169.254"}:
            return True
        # IP 地址判断 注意 ipaddress 不接受方括号包裹的 IPv6 但 hostname 已剥掉
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            # 普通域名 不在直拒列表 这里放过
            return False
    except Exception:
        # 任何意外都从严处理 直接拒
        return True


@tool
async def current_time(tz_offset_hours: int = 8) -> str:
    """获取当前时间 默认 UTC+8 北京时间

    入参
        tz_offset_hours 时区偏移整数 范围 -12 ~ 14 默认 +8

    返回 形如 "2026-05-21 19:30:00 (UTC+8)" 的字符串
    """
    tz = timezone(timedelta(hours=tz_offset_hours))
    now = datetime.now(tz)
    return now.strftime(f"%Y-%m-%d %H:%M:%S (UTC{tz_offset_hours:+d})")


@tool
async def http_get(url: str, timeout_s: float = _HTTP_TIMEOUT_S) -> str:
    """发起一次 HTTP GET 请求 拉取公开网页或 API 内容

    入参
        url 目标 URL 必须 http 或 https 公网地址
        timeout_s 超时秒数 默认 10

    返回 响应正文 最大 100KB 超出截断并加结尾标记
    安全 拒绝内网/回环/链路本地地址
    """
    if not url.startswith(("http://", "https://")):
        return f"[错误] URL 必须 http 或 https 开头 收到 {url}"
    if _is_internal_url(url):
        return f"[错误] 拒绝访问内网地址 {url}"
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
            if len(text) > _HTTP_MAX_BYTES:
                text = text[:_HTTP_MAX_BYTES] + "\n...(已截断)"
            return text
    except httpx.TimeoutException:
        return f"[错误] 请求超时 {timeout_s} 秒"
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        return f"[错误] HTTP {e.response.status_code}: {body}"
    except Exception as e:
        return f"[错误] {type(e).__name__}: {e}"


@tool
async def web_search(query: str, max_results: int = 5) -> str:
    """通过 DuckDuckGo HTML 接口做网络搜索 返回标题 链接 摘要

    入参
        query 搜索关键词
        max_results 最大结果数 默认 5 上限 10

    返回 多行结果字符串 每条形如 "1. 标题 | URL | 摘要"
    抓取容错 接口失败/HTML 结构变更时返回友好错误提示 不抛异常
    """
    max_results = min(max(max_results, 1), 10)
    url = "https://html.duckduckgo.com/html/"
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.post(url, data={"q": query})
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        _logger.warning("web_search 请求失败", error=str(e))
        return f"[错误] 搜索请求失败 {type(e).__name__}: {e}"

    # DDG html 模板形如
    # <a class="result__a" href="...">title</a>  ...  <a class="result__snippet">snippet</a>
    # 用宽松正则抽取 title link snippet 三段
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    matches = pattern.findall(html)
    if not matches:
        return f"[未找到结果] query={query}"

    out_lines: list[str] = []
    for i, (link, title, snippet) in enumerate(matches[:max_results], 1):
        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet_clean = re.sub(r"<[^>]+>", "", snippet).strip()
        # DDG 跳转链接形如 //duckduckgo.com/l/?uddg=<encoded>
        real = re.search(r"uddg=([^&]+)", link)
        if real:
            link = unquote(real.group(1))
        out_lines.append(f"{i}. {title_clean} | {link} | {snippet_clean[:120]}")
    return "\n".join(out_lines)


def get_shared_tools() -> list[Any]:
    """返回供 reply 阶段 deep_agent 挂载的共享 tool 列表"""
    return [current_time, http_get]


async def load_mcp_tools_from_db(storage: Any, *, owner_user_id: str | None = None) -> tuple[list[Any], list[str]]:
    """从数据库加载 MCP 工具 owner_user_id 过滤当前用户的配置

    owner_user_id 为 None 时跳过加载（系统启动场景）
    有值时从 mcp_servers 集合读取当前用户的配置
    返回 (工具列表, 已启用服务器名称列表)
    """
    import structlog
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import StdioConnection

    _mcp_logger = structlog.get_logger(__name__)

    if owner_user_id is None:
        return [], []

    servers = await storage.list_mcp_servers(owner_user_id=owner_user_id)
    if not servers:
        return [], []

    connections: dict[str, StdioConnection] = {}
    enabled_names: list[str] = []
    for srv in servers:
        if srv.disabled:
            _mcp_logger.info("mcp 跳过已禁用的 server", name=srv.name)
            continue
        if srv.transport != "stdio":
            _mcp_logger.info("mcp 跳过非 stdio server", name=srv.name, transport=srv.transport)
            continue

        command = srv.command or ""
        if not command:
            _mcp_logger.warning("mcp server 缺少 command 跳过", name=srv.name)
            continue

        conn: StdioConnection = {
            "transport": "stdio",
            "command": str(command),
            "args": [str(a) for a in srv.args] if isinstance(srv.args, list) else [],
        }
        if isinstance(srv.env, dict):
            conn["env"] = {str(k): str(v) for k, v in srv.env.items()}
        connections[srv.name] = conn
        enabled_names.append(srv.name)

    if not connections:
        return [], []

    try:
        client = MultiServerMCPClient(connections=connections)
    except Exception as e:
        _mcp_logger.warning("mcp client 初始化失败", error=str(e))
        return [], []

    # 逐个 server 拉工具 坏的跳过 好的继续挂载
    all_tools: list[Any] = []
    loaded_names: list[str] = []
    for name in enabled_names:
        try:
            server_tools = await client.get_tools(server_name=name)
        except Exception as e:
            # 加载状态写回: 标记失败
            existing = await storage.get_mcp_server(name, owner_user_id=owner_user_id)
            if existing is not None:
                existing.last_load_status = "failed"
                existing.last_load_error = str(e)
                existing.last_loaded_at = datetime.now(timezone.utc).isoformat()
                await storage.upsert_mcp_server(existing, owner_user_id=owner_user_id)
            _mcp_logger.warning("mcp 单个 server 工具加载失败 已跳过", name=name, error=str(e))
            continue
        all_tools.extend(server_tools)
        loaded_names.append(name)
        # 加载状态写回: 标记成功
        existing = await storage.get_mcp_server(name, owner_user_id=owner_user_id)
        if existing is not None:
            existing.last_load_status = "loaded"
            existing.last_load_error = ""
            existing.last_loaded_at = datetime.now(timezone.utc).isoformat()
            await storage.upsert_mcp_server(existing, owner_user_id=owner_user_id)

    if not loaded_names:
        return [], []

    _mcp_logger.info(
        "mcp 工具加载完成",
        owner_user_id=owner_user_id,
        server_count=len(loaded_names),
        tool_count=len(all_tools),
    )
    return all_tools, loaded_names


async def load_skills_from_db(storage: Any, *, owner_user_id: str | None = None) -> tuple[str, list[str]]:
    """从数据库加载已启用的 skills 拼接成 system prompt 追加内容

    owner_user_id 为 None 时跳过加载
    有值时从 skills 集合读取当前用户的配置
    返回 (拼接后的 system_prompt 文本, 已启用 skill 名称列表)
    """
    import structlog

    _skill_logger = structlog.get_logger(__name__)

    if owner_user_id is None:
        return "", []

    skills = await storage.list_skills(owner_user_id=owner_user_id)
    if not skills:
        return "", []

    enabled_parts: list[str] = []
    enabled_names: list[str] = []
    for s in skills:
        if not s.enabled:
            _skill_logger.info("skills 跳过已禁用的 skill", name=s.name)
            continue
        if not s.name or not s.content:
            _skill_logger.warning("skills 缺少 name 或 content 跳过", name=s.name)
            continue
        enabled_parts.append(f"## Skill: {s.name}\n\n{s.content}\n")
        enabled_names.append(s.name)

    if not enabled_parts:
        return "", []

    prefix = (
        "\n\n---\n\n"
        "## Skills（技能模块）\n\n"
        "以下是你被配置的专属技能模块 每个 skills 定义了一套标准操作流程\n"
        "遇到对应场景时必须遵守 skills 中的指令 优先级高于默认行为\n\n"
    )
    combined = prefix + "\n---\n\n".join(enabled_parts)
    _skill_logger.info(
        "skills 内容加载完成",
        owner_user_id=owner_user_id,
        enabled_count=len(enabled_names),
        names=enabled_names,
    )
    return combined, enabled_names

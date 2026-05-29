"""reply 阶段共享 tool 集合 deep_agent 调用时注入

设计要点
    - tool 是无状态函数 通过 langchain 的 RunnableConfig 注入 task_id agent_name 用于日志隔离
    - tool 之间不共享可变全局状态 涉及 IO 时 client 在函数内 with 创建
    - 安全 http_get/web_search 屏蔽内网 IP 防 SSRF
    - 谁创建谁使用 httpx.AsyncClient 在每次调用 with 内创建 与当前 loop 绑定 不跨线程复用
"""

from __future__ import annotations

import ipaddress
import json
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


def _build_backend_info_tool(settings: Any | None) -> Any:
    """构建后端信息查询工具 返回当前服务运行的地址和端口

    模型调用此工具获取后端 URL 后再用 http_request 调用具体接口
    闭包注入 settings 使工具能返回动态地址 不硬编码
    """
    from ..config import Settings

    real_settings: Settings | None = None
    if settings is not None and isinstance(settings, Settings):
        real_settings = settings

    @tool
    def backend_info() -> str:
        """查询当前后端服务运行地址

        返回后端 base URL 和端口等信息 用于 http_request 调用时拼接完整 URL
        也可以直接传相对路径给 http_request 如 /api/agents 工具会自动拼接

        返回 JSON 字符串 包含 base_url host port
        """
        if real_settings is None:
            return json.dumps({
                "base_url": "未配置",
                "host": "未知",
                "port": "未知",
                "hint": "当前未注入服务配置 请使用相对路径调用 http_request",
            })
        host = real_settings.server.host
        # 0.0.0.0 是监听所有网卡 对外访问用 127.0.0.1
        access_host = "127.0.0.1" if host == "0.0.0.0" else host
        port = real_settings.server.port
        base_url = f"http://{access_host}:{port}"
        return json.dumps({
            "base_url": base_url,
            "host": access_host,
            "port": port,
        })

    return backend_info


def _build_http_request_tool(
    storage: Any | None,
    settings: Any | None,
    owner_user_id: str | None,
) -> Any:
    """构建通用 HTTP 请求工具 自动注入本地后端鉴权 cookie

    闭包注入 storage/settings/owner_user_id 使工具内部无需 RunnableConfig 即可
    获取鉴权信息和后端端口配置

    支持相对路径: url 以 / 开头时自动拼接后端 base_url 如 /api/agents
    """
    from ..config import Settings

    # 类型窄化: settings 实际类型为 Settings 或 None
    real_settings: Settings | None = None
    if settings is not None and isinstance(settings, Settings):
        real_settings = settings

    @tool
    async def http_request(
        url: str,
        method: str = "GET",
        body: str = "",
        headers: str = "",
        timeout_s: float = _HTTP_TIMEOUT_S,
    ) -> str:
        """发起 HTTP 请求 支持 GET/POST/PUT/DELETE/PATCH 方法

        可用于调用后端 API 或访问外部 URL 调用本地后端时自动注入鉴权
        url 支持两种格式:
            - 相对路径: /api/agents 自动拼接后端地址并注入鉴权
            - 完整 URL: http://127.0.0.1:8002/api/agents

        先调用 backend_info 工具查看后端地址 或直接用相对路径调用更方便

        入参
            url 目标地址 支持相对路径(/api/agents)或完整 URL(http://...)
            method HTTP 方法 默认 GET 支持 GET/POST/PUT/DELETE/PATCH
            body 请求体 JSON 字符串 POST/PUT/PATCH 时使用
            headers 额外请求头 JSON 对象字符串 如 {"X-Custom": "value"}
            timeout_s 超时秒数 默认 10

        返回 响应状态码 + 正文 最大 100KB 超出截断
        安全 调用本地后端跳过 SSRF 防护 外部请求拒绝内网地址
        """
        # 1. 相对路径自动拼接后端 URL
        if url.startswith("/") and real_settings is not None:
            host = real_settings.server.host
            access_host = "127.0.0.1" if host == "0.0.0.0" else host
            port = real_settings.server.port
            url = f"http://{access_host}:{port}{url}"

        # 2. 校验 URL
        if not url.startswith(("http://", "https://")):
            return f"[错误] URL 格式不正确 支持相对路径(/api/xxx)或完整 URL(http://...) 收到 {url}"

        method = method.upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
            return f"[错误] 不支持的 HTTP 方法: {method}"

        # 3. 判断是否为本地后端请求
        is_local_backend = False
        if real_settings is not None:
            server_port = real_settings.server.port
            local_hosts = {"localhost", "127.0.0.1", "::1"}
            parsed = urlparse(url)
            host = parsed.hostname or ""
            port = parsed.port
            # URL 没有显式端口时 http 默认 80 https 默认 443
            # 后端默认端口 8002 不匹配 80/443 所以必须显式写端口
            if host in local_hosts and port == server_port:
                is_local_backend = True

        # 3. SSRF 防护 本地后端请求跳过 外部请求仍做检查
        if not is_local_backend and _is_internal_url(url):
            return f"[错误] 拒绝访问内网地址 {url}"

        # 4. 构建请求头
        req_headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        if headers:
            try:
                extra = json.loads(headers)
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        req_headers[str(k)] = str(v)
            except json.JSONDecodeError:
                return f"[错误] headers 不是合法 JSON: {headers[:100]}"

        # 5. 本地后端请求自动注入鉴权 cookie
        if is_local_backend and storage is not None and owner_user_id and real_settings is not None:
            try:
                cookie_name = real_settings.auth.session_cookie_name
                session = await storage.create_auth_session(
                    owner_user_id,
                    expires_in_hours=real_settings.auth.session_ttl_hours,
                )
                req_headers["Cookie"] = f"{cookie_name}={session.session_id}"
            except Exception as e:
                _logger.warning("http_request 自动鉴权失败", error=str(e))
                return f"[错误] 自动鉴权失败: {type(e).__name__}: {e}"

        # 6. 发送请求
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s,
                follow_redirects=True,
            ) as client:
                req_kwargs: dict[str, Any] = {"headers": req_headers}
                if method in {"POST", "PUT", "PATCH"} and body:
                    req_kwargs["content"] = body
                    if "Content-Type" not in req_headers:
                        req_headers["Content-Type"] = "application/json"

                resp = await client.request(method, url, **req_kwargs)
                text = resp.text
                if len(text) > _HTTP_MAX_BYTES:
                    text = text[:_HTTP_MAX_BYTES] + "\n...(已截断)"
                return f"[HTTP {resp.status_code}]\n{text}"
        except httpx.TimeoutException:
            return f"[错误] 请求超时 {timeout_s} 秒"
        except httpx.HTTPStatusError as e:
            resp_body = e.response.text[:200] if e.response is not None else ""
            return f"[错误] HTTP {e.response.status_code}: {resp_body}"
        except Exception as e:
            return f"[错误] {type(e).__name__}: {e}"

    return http_request


def get_shared_tools(
    storage: Any | None = None,
    settings: Any | None = None,
    owner_user_id: str | None = None,
    object_store: Any | None = None,
) -> list[Any]:
    """返回供 reply 阶段 deep_agent 挂载的共享 tool 列表

    storage/settings/owner_user_id 用于构建 HTTP 工具的鉴权上下文
    object_store 用于构建 skill 脚本执行工具
    为 None 时构建无鉴权版本 仅支持外部 URL 请求 不支持本地后端调用
    """
    tools: list[Any] = [current_time]
    # 后端信息查询工具: 返回服务运行地址 模型先用此工具获取 URL
    tools.append(_build_backend_info_tool(settings))
    # 通用 HTTP 请求工具: 支持相对路径和完整 URL 自动注入鉴权
    tools.append(_build_http_request_tool(storage, settings, owner_user_id))
    # skill 脚本执行工具: 执行 skill 包中的 Python 脚本
    if object_store is not None and owner_user_id is not None:
        tools.append(_build_execute_skill_script_tool(storage, object_store, owner_user_id))
    return tools


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
        # 有附带文件时追加可用脚本清单说明 引导模型调用 execute_skill_script 工具
        if s.files:
            file_list = "\n".join([f"- {f.path}" for f in s.files])
            enabled_parts.append(
                f"## Skill: {s.name}\n\n{s.content}\n\n"
                f"**可用脚本文件**:\n{file_list}\n\n"
                f"使用 `execute_skill_script(skill_name=\"{s.name}\", script_path=\"xxx\")` 工具执行脚本\n"
            )
        else:
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


def _build_execute_skill_script_tool(storage: Any, object_store: Any, owner_user_id: str) -> Any:
    """构建 skill 脚本执行工具闭包

    工具从 DB 加载 skill 配置 → 在 files 列表中查找脚本 → 从 MinIO 读内容
    → 写入临时目录保留原始目录结构 → asyncio 子进程执行 → 返回 stdout/stderr
    """
    import asyncio
    import os
    import tempfile

    import structlog

    _exec_logger = structlog.get_logger(__name__)

    # 脚本执行超时(秒)
    _SCRIPT_TIMEOUT_S = 30

    @tool
    async def execute_skill_script(
        skill_name: str,
        script_path: str,
        script_args: list[str] | None = None,
    ) -> str:
        """执行 skill 包中的 Python 脚本

        Args:
            skill_name: skill 名称 如 "news-skill"
            script_path: 脚本在 skill 包内的相对路径 如 "scripts/fetch.py"
            script_args: 传递给脚本的命令行参数列表(可选)

        Returns:
            脚本的 stdout 输出 如果执行失败返回包含 stderr 的错误信息
        """
        # 1. 从 DB 加载 skill 校验存在且已启用
        skill = await storage.get_skill(skill_name, owner_user_id=owner_user_id)
        if skill is None:
            return f"错误: skill 不存在 skill_name={skill_name}"
        if not skill.enabled:
            return f"错误: skill 已禁用 skill_name={skill_name}"

        # 2. 在 files 列表中查找脚本
        file_meta = None
        for f in skill.files:
            if f.path == script_path:
                file_meta = f
                break
        if file_meta is None:
            return f"错误: 脚本不存在 script_path={script_path} 可用文件: {[f.path for f in skill.files]}"

        # 3. 校验文件扩展名 只允许执行 .py 文件
        if not script_path.endswith(".py"):
            return f"错误: 只允许执行 .py 文件 script_path={script_path}"

        # 4. 从 MinIO 读取脚本内容
        try:
            stored = await object_store.get_bytes(file_meta.object_key)
            script_content = stored.content
        except Exception as e:
            _exec_logger.error("脚本读取失败", skill_name=skill_name, script_path=script_path, error=str(e))
            return f"错误: 脚本文件读取失败 {e}"

        # 5. 写入临时目录 保留原始目录结构确保相对路径引用正确
        # 临时目录格式: /tmp/skill_exec_{skill_name}_{random}
        tmp_dir = tempfile.mkdtemp(prefix=f"skill_exec_{skill_name}_")
        script_full_path = os.path.join(tmp_dir, script_path)
        script_dir = os.path.dirname(script_full_path)
        os.makedirs(script_dir, exist_ok=True)

        try:
            with open(script_full_path, "wb") as f:
                f.write(script_content)

            # 6. 构建执行命令
            cmd_args = ["python3", script_full_path]
            if script_args:
                cmd_args.extend(script_args)

            # 7. 子进程执行 设置超时 工作目录为 skill 包根目录(保留相对路径关系)
            _exec_logger.info("开始执行脚本", skill_name=skill_name, script_path=script_path, args=script_args)
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SCRIPT_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                _exec_logger.warning("脚本执行超时", skill_name=skill_name, script_path=script_path)
                return f"错误: 脚本执行超时({_SCRIPT_TIMEOUT_S}秒)"

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                _exec_logger.warning(
                    "脚本执行失败",
                    skill_name=skill_name,
                    script_path=script_path,
                    returncode=proc.returncode,
                    stderr=stderr_text[:500],
                )
                return f"脚本执行失败(退出码={proc.returncode})\nstderr:\n{stderr_text}\nstdout:\n{stdout_text}"

            _exec_logger.info("脚本执行成功", skill_name=skill_name, script_path=script_path, output_len=len(stdout_text))
            return stdout_text

        finally:
            # 8. 清理临时目录
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    return execute_skill_script

"""MCP 配置 API  单个服务器的 CRUD + 全量 JSON 读写

GET    /api/mcp/config          返回当前用户的 MCP 配置 JSON
PUT    /api/mcp/config          全量覆盖当前用户的 MCP 配置
GET    /api/mcp/servers         列出当前用户的 MCP 服务器（表格管理用）
POST   /api/mcp/servers         新增一个 MCP 服务器
PUT    /api/mcp/servers/{name}  修改单个 MCP 服务器
DELETE /api/mcp/servers/{name}  删除单个 MCP 服务器
PUT    /api/mcp/servers/{name}/toggle  快捷启停开关

数据隔离: 每个 MCP 服务器严格属于创建用户 owner_user_id 过滤
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth_context import get_current_identity
from ..core.models import McpServerConfig, RequestIdentity

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ====== JSON 全量读写（保留原接口 兼容旧前端） ======

class McpConfigResponse(BaseModel):
    """GET /api/mcp/config 响应  返回整份 JSON"""

    config: dict
    """完整的 mcpServers JSON 对象 空时返回 {}"""


class McpConfigRequest(BaseModel):
    """PUT /api/mcp/config 请求  全量覆盖"""

    config: dict
    """前端提交的完整 mcpServers JSON 后端不做结构校验"""


@router.get("/config", response_model=McpConfigResponse)
async def get_mcp_config(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> McpConfigResponse:
    """读取当前用户的 MCP 配置  不存在返回空 {}"""
    storage = request.app.state.storage
    servers = await storage.list_mcp_servers(owner_user_id=identity.user_id)
    mcp_servers = {}
    for s in servers:
        # 还原为 mcpServers map 格式 key 是 name
        mcp_servers[s.name] = _server_to_storage_dict(s)
    return McpConfigResponse(config={"mcpServers": mcp_servers})


@router.put("/config", response_model=McpConfigResponse)
async def put_mcp_config(
    body: McpConfigRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> McpConfigResponse:
    """全量覆盖当前用户的 MCP 配置  前端提交的 JSON 直接落库"""
    storage = request.app.state.storage
    config = body.config
    if not isinstance(config, dict):
        return McpConfigResponse(config={})

    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    # 先删除当前用户所有旧服务器
    old_servers = await storage.list_mcp_servers(owner_user_id=identity.user_id)
    for old in old_servers:
        await storage.delete_mcp_server(old.name, owner_user_id=identity.user_id)

    # 再写入新服务器
    for name, srv_data in mcp_servers.items():
        if not isinstance(srv_data, dict):
            continue
        server = McpServerConfig(
            name=name,
            transport=srv_data.get("transport", "stdio"),
            command=srv_data.get("command"),
            args=srv_data.get("args", []),
            env=srv_data.get("env", {}),
            url=srv_data.get("url"),
            headers=srv_data.get("headers", {}),
            always_allow=srv_data.get("alwaysAllow", srv_data.get("always_allow", [])),
            disabled=srv_data.get("disabled", False),
            owner_user_id=identity.user_id,
        )
        await storage.upsert_mcp_server(server, owner_user_id=identity.user_id)

    return McpConfigResponse(config=body.config)


# ====== 单个服务器 CRUD（表格管理模式） ======

class McpServerItem(BaseModel):
    """单个 MCP 服务器的配置视图"""

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    url: str | None = None
    headers: dict[str, str] = {}
    always_allow: list[str] = []
    disabled: bool = False
    updated_at: str = ""
    last_load_status: str = ""
    last_load_error: str = ""
    last_loaded_at: str = ""


class McpServerUpdate(BaseModel):
    """PUT /api/mcp/servers/{name} 请求体"""

    transport: str = "stdio"
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    url: str | None = None
    headers: dict[str, str] = {}
    always_allow: list[str] = []
    disabled: bool = False


class McpServerToggle(BaseModel):
    """PUT /api/mcp/servers/{name}/toggle 请求体"""

    disabled: bool


class McpServersListResponse(BaseModel):
    """GET /api/mcp/servers 响应"""

    servers: list[McpServerItem]


def _server_to_item(s: McpServerConfig) -> McpServerItem:
    """McpServerConfig → McpServerItem"""
    return McpServerItem(
        name=s.name,
        transport=s.transport,
        command=s.command,
        args=s.args,
        env=s.env,
        url=s.url,
        headers=s.headers,
        always_allow=s.always_allow,
        disabled=s.disabled,
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
        last_load_status="",  # 加载状态暂不持久化到模型
        last_load_error="",
        last_loaded_at="",
    )


def _server_to_storage_dict(s: McpServerConfig) -> dict:
    """McpServerConfig → mcpServers map 存储格式 (不含 name key)"""
    return {
        "transport": s.transport,
        "command": s.command,
        "args": s.args,
        "env": s.env,
        "url": s.url,
        "headers": s.headers,
        "alwaysAllow": s.always_allow,
        "always_allow": s.always_allow,
        "disabled": s.disabled,
        "updated_at": s.updated_at.isoformat() if s.updated_at else _utcnow().isoformat(),
    }


# ====== 固定路径路由必须在带参数路由之前定义 ======

@router.get("/servers", response_model=McpServersListResponse)
async def list_mcp_servers(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> McpServersListResponse:
    """列出当前用户的 MCP 服务器"""
    storage = request.app.state.storage
    servers = await storage.list_mcp_servers(owner_user_id=identity.user_id)
    return McpServersListResponse(servers=[_server_to_item(s) for s in servers])


@router.post("/servers", response_model=McpServerItem)
async def create_mcp_server(
    body: McpServerItem,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> McpServerItem:
    """新增一个 MCP 服务器  同名抛 409"""
    storage = request.app.state.storage
    # 检查同名是否已存在 (严格按 owner_user_id)
    existing = await storage.get_mcp_server(body.name, owner_user_id=identity.user_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"MCP 服务器已存在 name={body.name}")

    server = McpServerConfig(
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        headers=body.headers,
        always_allow=body.always_allow,
        disabled=body.disabled,
        owner_user_id=identity.user_id,
    )
    await storage.upsert_mcp_server(server, owner_user_id=identity.user_id)
    return body


@router.put("/servers/{name}", response_model=McpServerItem)
async def update_mcp_server(
    name: str,
    body: McpServerUpdate,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> McpServerItem:
    """修改单个 MCP 服务器  全量覆盖  不存在抛 404"""
    storage = request.app.state.storage
    existing = await storage.get_mcp_server(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"MCP 服务器不存在 name={name}")

    server = McpServerConfig(
        name=name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        headers=body.headers,
        always_allow=body.always_allow,
        disabled=body.disabled,
        owner_user_id=identity.user_id,
    )
    await storage.upsert_mcp_server(server, owner_user_id=identity.user_id)
    return _server_to_item(server)


@router.put("/servers/{name}/toggle", response_model=McpServerItem)
async def toggle_mcp_server(
    name: str,
    body: McpServerToggle,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> McpServerItem:
    """快捷启停开关  只改 disabled 字段"""
    storage = request.app.state.storage
    existing = await storage.get_mcp_server(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"MCP 服务器不存在 name={name}")

    existing.disabled = body.disabled
    await storage.upsert_mcp_server(existing, owner_user_id=identity.user_id)
    return _server_to_item(existing)


@router.delete("/servers/{name}", status_code=204)
async def delete_mcp_server(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> None:
    """删除单个 MCP 服务器"""
    storage = request.app.state.storage
    try:
        await storage.delete_mcp_server(name, owner_user_id=identity.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reload")
async def reload_agents_for_mcp(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> dict:
    """重载当前用户的 agent 实例让 MCP 配置变更立刻生效"""
    registry = request.app.state.deep_agents
    count = await registry.reload_all(owner_user_id=identity.user_id)
    return {"reloaded": count}
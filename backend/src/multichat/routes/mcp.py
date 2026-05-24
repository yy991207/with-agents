"""MCP 配置 API  整份 JSON 的读取与保存 + 单个服务器的 CRUD

GET    /api/mcp/config          返回 settings 集合中 mcp_config 文档的原始 JSON
PUT    /api/mcp/config          全量覆盖 mcp_config
GET    /api/mcp/servers         列出所有 MCP 服务器（表格管理用）
POST   /api/mcp/servers         新增一个 MCP 服务器
PUT    /api/mcp/servers/{name}  修改单个 MCP 服务器
DELETE /api/mcp/servers/{name}  删除单个 MCP 服务器
PUT    /api/mcp/servers/{name}/toggle  快捷启停开关

数据格式对齐 mcp_settings.json:
    {"mcpServers": {"name": {"command":"npx","args":[...],"env":{...},"alwaysAllow":[...],"disabled":false}, ...}}
JSON 编辑接口（/config）不做结构校验 仅做 JSON parse 检查格式合法性
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# 固定 _id 用于 settings 集合中的 MCP 配置文档
_MCP_CONFIG_DOC_ID = "mcp_config"


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
async def get_mcp_config(request: Request) -> McpConfigResponse:
    """读取当前 MCP 配置  不存在返回空 {}"""
    storage = request.app.state.storage
    doc = await storage._db["settings"].find_one({"_id": _MCP_CONFIG_DOC_ID})
    if doc is None:
        return McpConfigResponse(config={})
    config = doc.get("config", {})
    if not isinstance(config, dict):
        return McpConfigResponse(config={})
    return McpConfigResponse(config=config)


@router.put("/config", response_model=McpConfigResponse)
async def put_mcp_config(
    body: McpConfigRequest, request: Request
) -> McpConfigResponse:
    """全量覆盖 MCP 配置  前端提交的 JSON 直接落库"""
    storage = request.app.state.storage
    await storage._db["settings"].update_one(
        {"_id": _MCP_CONFIG_DOC_ID},
        {"$set": {"config": body.config}},
        upsert=True,
    )
    return McpConfigResponse(config=body.config)


# ====== 单个服务器 CRUD（表格管理模式） ======

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class McpServerItem(BaseModel):
    """单个 MCP 服务器的配置视图  字段对齐 models.McpServerConfig"""

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


class McpServerUpdate(BaseModel):
    """PUT /api/mcp/servers/{name} 请求体  全量覆盖除 name 外的字段"""

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


def _config_collection(storage):
    """返回 settings 集合 封装访问路径"""
    return storage._db["settings"]


async def _ensure_doc(storage):
    """确保 mcp_config 文档存在 不存在则创建空文档"""
    col = _config_collection(storage)
    doc = await col.find_one({"_id": _MCP_CONFIG_DOC_ID})
    if doc is None:
        await col.insert_one({"_id": _MCP_CONFIG_DOC_ID, "config": {"mcpServers": {}}})
        doc = {"_id": _MCP_CONFIG_DOC_ID, "config": {"mcpServers": {}}}
    # 确保 config.mcpServers 是 dict
    config = doc.get("config", {})
    if not isinstance(config, dict):
        config = {}
    if "mcpServers" not in config or not isinstance(config.get("mcpServers"), dict):
        config["mcpServers"] = {}
        await col.update_one(
            {"_id": _MCP_CONFIG_DOC_ID},
            {"$set": {"config": config}},
        )
        doc["config"] = config
    return doc


def _server_to_item(server_config: dict) -> McpServerItem:
    """将 mcpServers 中的原始 dict 转成 McpServerItem 视图"""
    return McpServerItem(
        name=server_config.get("name", ""),
        transport=server_config.get("transport", "stdio"),
        command=server_config.get("command"),
        args=server_config.get("args", []),
        env=server_config.get("env", {}),
        url=server_config.get("url"),
        headers=server_config.get("headers", {}),
        always_allow=server_config.get("alwaysAllow", server_config.get("always_allow", [])),
        disabled=server_config.get("disabled", False),
        updated_at=server_config.get("updated_at", ""),
    )


def _item_to_server(item: McpServerItem) -> dict:
    """将 McpServerItem 转回 mcpServers 存储格式"""
    return {
        "name": item.name,
        "transport": item.transport,
        "command": item.command,
        "args": item.args,
        "env": item.env,
        "url": item.url,
        "headers": item.headers,
        "alwaysAllow": item.always_allow,
        "always_allow": item.always_allow,
        "disabled": item.disabled,
        "updated_at": item.updated_at or _utcnow().isoformat(),
    }


# ====== 固定路径路由必须在带参数路由之前定义 ======

@router.get("/servers", response_model=McpServersListResponse)
async def list_mcp_servers(request: Request) -> McpServersListResponse:
    """列出所有 MCP 服务器"""
    doc = await _ensure_doc(request.app.state.storage)
    config = doc.get("config", {})
    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return McpServersListResponse(servers=[])

    servers: list[McpServerItem] = []
    for name, server_data in mcp_servers.items():
        if not isinstance(server_data, dict):
            continue
        server_data["name"] = name
        servers.append(_server_to_item(server_data))

    return McpServersListResponse(servers=servers)


@router.post("/servers", response_model=McpServerItem)
async def create_mcp_server(body: McpServerItem, request: Request) -> McpServerItem:
    """新增一个 MCP 服务器  name 不可重复"""
    storage = request.app.state.storage
    col = _config_collection(storage)
    doc = await _ensure_doc(storage)

    config = doc.get("config", {})
    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    if body.name in mcp_servers:
        raise HTTPException(status_code=409, detail=f"MCP 服务器已存在 name={body.name}")

    body.updated_at = _utcnow().isoformat()
    mcp_servers[body.name] = _item_to_server(body)
    del mcp_servers[body.name]["name"]  # mcpServers key 就是 name 不再冗余存

    await col.update_one(
        {"_id": _MCP_CONFIG_DOC_ID},
        {"$set": {"config.mcpServers": mcp_servers}},
    )
    return body


@router.put("/servers/{name}", response_model=McpServerItem)
async def update_mcp_server(name: str, body: McpServerUpdate, request: Request) -> McpServerItem:
    """修改单个 MCP 服务器  全量覆盖除 name 外的字段  name 不存在抛 404"""
    storage = request.app.state.storage
    col = _config_collection(storage)
    doc = await _ensure_doc(storage)

    config = doc.get("config", {})
    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict) or name not in mcp_servers:
        raise HTTPException(status_code=404, detail=f"MCP 服务器不存在 name={name}")

    updated = McpServerItem(
        name=name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        headers=body.headers,
        always_allow=body.always_allow,
        disabled=body.disabled,
        updated_at=_utcnow().isoformat(),
    )
    mcp_servers[name] = _item_to_server(updated)
    del mcp_servers[name]["name"]

    await col.update_one(
        {"_id": _MCP_CONFIG_DOC_ID},
        {"$set": {"config.mcpServers": mcp_servers}},
    )
    return updated


@router.put("/servers/{name}/toggle", response_model=McpServerItem)
async def toggle_mcp_server(name: str, body: McpServerToggle, request: Request) -> McpServerItem:
    """快捷启停开关  只改 disabled 字段  name 不存在抛 404"""
    storage = request.app.state.storage
    col = _config_collection(storage)
    doc = await _ensure_doc(storage)

    config = doc.get("config", {})
    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict) or name not in mcp_servers:
        raise HTTPException(status_code=404, detail=f"MCP 服务器不存在 name={name}")

    server_data = mcp_servers[name]
    if not isinstance(server_data, dict):
        raise HTTPException(status_code=404, detail=f"MCP 服务器数据异常 name={name}")

    server_data["disabled"] = body.disabled
    mcp_servers[name] = server_data

    await col.update_one(
        {"_id": _MCP_CONFIG_DOC_ID},
        {"$set": {"config.mcpServers": mcp_servers}},
    )

    server_data["name"] = name
    return _server_to_item(server_data)


@router.delete("/servers/{name}", status_code=204)
async def delete_mcp_server(name: str, request: Request) -> None:
    """删除单个 MCP 服务器  name 不存在抛 404"""
    storage = request.app.state.storage
    col = _config_collection(storage)
    doc = await _ensure_doc(storage)

    config = doc.get("config", {})
    mcp_servers = config.get("mcpServers", {})
    if not isinstance(mcp_servers, dict) or name not in mcp_servers:
        raise HTTPException(status_code=404, detail=f"MCP 服务器不存在 name={name}")

    del mcp_servers[name]

    await col.update_one(
        {"_id": _MCP_CONFIG_DOC_ID},
        {"$set": {"config.mcpServers": mcp_servers}},
    )
"""MCP 配置 API  整份 JSON 的读取与保存

GET  /api/mcp/config   返回 settings 集合中 mcp_config 文档的原始 JSON
PUT  /api/mcp/config   全量覆盖 mcp_config  JSON 合法性由前端自行保证

数据格式对齐 mcp_settings.json:
    {"mcpServers": {"name": {"command":"npx","args":[...],"env":{...},"alwaysAllow":[...],"disabled":false}, ...}}
后端不对 JSON 结构做任何校验  仅做 JSON parse 检查格式合法性
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# 固定 _id 用于 settings 集合中的 MCP 配置文档
_MCP_CONFIG_DOC_ID = "mcp_config"


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
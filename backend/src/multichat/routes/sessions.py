"""GET /sessions 列出最近 N 个 session 用于侧边栏

无路径参数 query 可选 limit 控制条数 默认 50
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="", tags=["history"])


@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """列出最近会话 给前端左侧栏初始化用 按 updated_at 降序"""
    storage = request.app.state.storage
    metas = await storage.list_sessions(limit=limit)
    return [m.model_dump(mode="json") for m in metas]

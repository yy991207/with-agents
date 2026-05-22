"""GET /sessions 列出最近 N 个 session 用于侧边栏

无路径参数 query 可选 limit 控制条数 默认 50

DELETE /sessions/{session_id} 删除 session 与其下所有 rounds
    - 204 删除成功
    - 404 session 不存在
    - 409 session 下还有进行中 round 不允许删除
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

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


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request) -> None:
    """删除指定 session 及其下所有 rounds

    错误码:
        404 session 不存在
        409 session 下还有进行中的 round 提示用户先取消或等其完成
    """
    storage = request.app.state.storage
    try:
        await storage.delete_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    except ValueError as e:
        # 进行中 round 阻塞删除 返回 409 让前端提示用户
        raise HTTPException(status_code=409, detail=str(e))

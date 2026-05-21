"""GET /history/{session_id} 拉一个 session 全部历史 round"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="", tags=["history"])


@router.get("/history/{session_id}")
async def get_history(session_id: str, request: Request) -> dict:
    """获取指定会话的所有轮次详情 给前端进入历史会话时回灌

    返回结构
        {
            "session": Session,                  # 会话元信息
            "rounds":  [Round, Round, ...]       # 时间正序
        }
    """
    storage = request.app.state.storage
    session = await storage.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"session not found: {session_id}")
    rounds = await storage.list_rounds(session_id)
    return {
        "session": session.model_dump(mode="json"),
        "rounds": [r.model_dump(mode="json") for r in rounds],
    }

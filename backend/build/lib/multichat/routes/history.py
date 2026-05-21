"""历史查询路由骨架

提供两类接口:
    - GET /sessions: 列出最近会话 用于左侧栏
    - GET /history/{session_id}: 获取单个会话所有 round 详情
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["history"])


@router.get("/sessions")
async def list_sessions() -> dict[str, list]:
    """列出最近会话"""
    raise NotImplementedError("M2 实施")


@router.get("/history/{session_id}")
async def get_history(session_id: str) -> dict[str, list]:
    """获取指定会话的所有轮次详情"""
    raise NotImplementedError("M2 实施")

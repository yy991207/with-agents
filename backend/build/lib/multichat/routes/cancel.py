"""POST /cancel 路由骨架

请求体:
    - task_id

行为:
    - 标记任务为 cancelled 取消未完成的 think/reply 协程并通过 SSE 推送终止事件
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["cancel"])


@router.post("/cancel")
async def cancel() -> dict[str, str]:
    """取消进行中的任务"""
    raise NotImplementedError("M2 实施")

"""GET /sse/{task_id} 路由骨架

行为:
    - 通过 sse-starlette 暴露 SSE 流
    - 内部消费 SSEStream.iter_events 由 TaskManager 注入
    - 客户端断开时优雅释放资源
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["stream"])


@router.get("/sse/{task_id}")
async def stream(task_id: str) -> dict[str, str]:
    """订阅指定 task_id 的事件流"""
    raise NotImplementedError("M2 实施")

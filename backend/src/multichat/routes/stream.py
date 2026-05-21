"""GET /sse/{task_id} SSE 事件流

行为
    - 通过 task_manager.get_hub 拿到 TaskEventHub
    - 把 hub 桥接到 sse-starlette EventSourceResponse 由它输出 text/event-stream
    - 客户端断开时 stream_hub 的 finally 段会调用 hub.unsubscribe 释放队列

错误
    - 404 task_id 对应的 hub 已不在内存(任务结束/进程重启) 此时前端应改用
      /history/{session_id} 拉取 round 终态
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..core.sse import build_sse_response

router = APIRouter(prefix="", tags=["chat"])


@router.get("/sse/{task_id}")
async def stream(task_id: str, request: Request):
    """订阅指定 task_id 的事件流"""
    tm = request.app.state.task_manager
    hub = tm.get_hub(task_id)
    if hub is None:
        # 任务已结束或进程曾重启 内存 hub 丢失 让前端走 history 兜底
        raise HTTPException(
            404, "task hub not found may have completed or unrecoverable"
        )
    return build_sse_response(hub)

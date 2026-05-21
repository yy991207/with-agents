"""POST /cancel 全局或单 agent 取消

请求体
    - task_id
    - scope: "global" 表示整体取消 或 agent 名表示只取消该 agent 的 think 子任务

响应
    - 204 取消已发出 后续 SSE 会推送终止事件
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class CancelRequest(BaseModel):
    """取消请求"""

    task_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)  # "global" 或具体 agent 名


@router.post("/cancel", status_code=204)
async def cancel(body: CancelRequest, request: Request) -> None:
    """取消进行中的任务或单个 agent 子任务"""
    tm = request.app.state.task_manager
    # 任务不存在或已结束 task_manager 实现里多半幂等吃掉 这里不抛异常
    await tm.cancel_task(body.task_id, body.scope)

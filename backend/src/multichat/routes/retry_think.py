"""POST /retry-think 重试某 agent 的 think

请求体
    - task_id
    - agent: 单 agent 名 表示重启该 agent 的 think 子任务

响应
    - 204 重试已发出
    - 501 单卡 retry 在 M2 暂未实装 task_manager 抛 NotImplementedError 时返回

设计说明
    - 整体重试可以走 /decide choice="regenerate" 此处仅处理单 agent 重试
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class RetryThinkRequest(BaseModel):
    """单 agent 重试请求"""

    task_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)


@router.post("/retry-think", status_code=204)
async def retry_think(body: RetryThinkRequest, request: Request) -> None:
    """重启指定 agent 的 think 子任务"""
    tm = request.app.state.task_manager
    try:
        await tm.retry_think(body.task_id, body.agent)
    except NotImplementedError:
        # M2 暂未实装 单 agent retry 给前端一个明确的 501 提示走 regenerate 兜底
        raise HTTPException(
            501, "single-agent retry not yet implemented try regenerate"
        )

"""POST /decide 用户决策

请求体
    - task_id: 目标任务 id
    - choice: 用户决策 可能取值
        * 某个 agent 名 表示选这个 agent 进入 reply 阶段
        * "regenerate" 表示重新触发 4 卡 think
        * "auto" 表示交给 judge 自动选发言者

响应
    - 204 表示决策提交成功 后续状态变化通过 SSE 推送

错误码
    - 409 task 当前不在等决策 或 task_id 未知 task_manager 抛 KeyError
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class DecideRequest(BaseModel):
    """决策请求"""

    task_id: str = Field(min_length=1)
    choice: str = Field(min_length=1)


@router.post("/decide", status_code=204)
async def decide(body: DecideRequest, request: Request) -> None:
    """提交决策 触发 reply 阶段或重新 think"""
    tm = request.app.state.task_manager
    try:
        await tm.submit_decision(body.task_id, body.choice)
    except KeyError:
        # 任务不存在或当前不是 THINK_DONE/WAITING_DECISION 状态
        raise HTTPException(409, "task not awaiting decision or unknown")

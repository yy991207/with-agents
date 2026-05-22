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
    - 429 choice=auto 触发 judge 软限流  防止用户狂点 "帮我选" 打爆 LLM
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.rate_limit import RateLimitExceeded

router = APIRouter(prefix="", tags=["chat"])


class DecideRequest(BaseModel):
    """决策请求"""

    task_id: str = Field(min_length=1)
    choice: str = Field(min_length=1)


@router.post("/decide", status_code=204)
async def decide(body: DecideRequest, request: Request) -> None:
    """提交决策 触发 reply 阶段或重新 think

    choice == auto 时进入 judge 软限流  超阈值 429
        路由层不知道实际有没有走 judge 只能在路由层先做计数
    """
    if body.choice == "auto":
        limiter = getattr(request.app.state, "judge_limiter", None)
        # 测试或老的应用工厂可能没挂 limiter  缺失则跳过限频
        if limiter is not None:
            try:
                await limiter.check()
            except RateLimitExceeded as e:
                raise HTTPException(status_code=429, detail=str(e))

    tm = request.app.state.task_manager
    try:
        await tm.submit_decision(body.task_id, body.choice)
    except KeyError:
        # 任务不存在或当前不是 THINK_DONE/WAITING_DECISION 状态
        raise HTTPException(409, "task not awaiting decision or unknown")

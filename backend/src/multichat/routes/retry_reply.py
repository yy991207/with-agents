"""POST /retry_reply 单 agent 重答

请求体
    - task_id: 目标任务 id
    - agent: 要重答的 agent name  必须在 round.agents 内

响应
    - 204 重答已发起 后续 reply.start / reply.chunk / reply.done 通过 SSE 推送

错误码
    - 404 task / round 不存在
    - 409 round 状态非 DONE  或 agent 不在候选

注意
    - 重答会清空 round.selected_reply_agent (即便选过)  逼前端重新确认
    - 重答期间 round.state 会切回 REPLYING  其他 agent 内容不变
    - 完成后所有 agent 都 done 时再切回 DONE
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class RetryReplyRequest(BaseModel):
    """重答请求"""

    task_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)


@router.post("/retry_reply", status_code=204)
async def retry_reply(body: RetryReplyRequest, request: Request) -> None:
    """对单个 agent 触发重答  其它 agent 不动"""
    tm = request.app.state.task_manager
    try:
        await tm.retry_reply(body.task_id, body.agent)
    except KeyError:
        raise HTTPException(404, "task or round not found")
    except ValueError as e:
        raise HTTPException(409, str(e))

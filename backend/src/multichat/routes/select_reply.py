"""POST /select_reply 用户从多 agent 候选中选定一个回答

请求体
    - task_id: 目标任务 id
    - agent: 要选中的 agent name  必须在 round.agents 内 且对应 reply 状态为 done

响应
    - 204 选答成功 后续状态变化通过 SSE reply.selected 推送

错误码
    - 404 task / round 不存在
    - 409 round 状态非 DONE 或 agent 不在候选 / reply 未完成
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class SelectReplyRequest(BaseModel):
    """选答请求"""

    task_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)


@router.post("/select_reply", status_code=204)
async def select_reply(body: SelectReplyRequest, request: Request) -> None:
    """提交选答  把用户选定 agent 落库  让下一轮 history 拼接以此 agent 的 content 为准"""
    tm = request.app.state.task_manager
    try:
        await tm.select_reply(body.task_id, body.agent)
    except KeyError:
        raise HTTPException(404, "task or round not found")
    except ValueError as e:
        raise HTTPException(409, str(e))

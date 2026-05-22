"""POST /retry-think 重试某 agent 的 think

请求体
    - task_id
    - agent: 单 agent 名 表示重启该 agent 的 think 子任务

响应
    - 204 重试已发出
    - 404 task 不存在或已结束(KeyError)
    - 409 状态机不允许 retry 例如 task 不在 THINK_DONE / agent 不存在(ValueError)
    - 501 retry 接口暂未实装(NotImplementedError) 留作灰度回滚兜底

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
    except KeyError:
        # task 已结束/不存在 让前端清理本地状态
        raise HTTPException(404, f"task not found or not active: {body.task_id}")
    except ValueError as e:
        # 状态机不允许 例如不在 THINK_DONE 或 agent 名错
        raise HTTPException(409, str(e))
    except NotImplementedError:
        # 兜底 任何回滚到旧版 task_manager 的场景给个友好 501
        raise HTTPException(
            501, "single-agent retry not yet implemented try regenerate"
        )

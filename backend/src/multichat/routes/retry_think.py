"""POST /retry-think 路由骨架

请求体:
    - task_id
    - agent_name: 可选 不传则全部重试

行为:
    - 针对失败或超时的 think 结果重新触发对应 agent 的 think 调用
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["retry"])


@router.post("/retry-think")
async def retry_think() -> dict[str, str]:
    """重新触发 think 阶段"""
    raise NotImplementedError("M2 实施")

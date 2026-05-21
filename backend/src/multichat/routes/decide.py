"""POST /decide 路由骨架

请求体:
    - task_id
    - chosen_agent: 用户选择的发言者名称

响应:
    - 200 OK 后端开始推进 reply 阶段
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["decide"])


@router.post("/decide")
async def decide() -> dict[str, str]:
    """提交用户决策 触发 reply 阶段"""
    raise NotImplementedError("M2 实施")

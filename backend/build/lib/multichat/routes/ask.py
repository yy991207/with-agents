"""POST /ask 路由骨架

请求体:
    - session_id: 可选 不传则新建会话
    - question: 用户问题文本

响应:
    - task_id: 用于后续 SSE 订阅与 decide cancel 操作
    - 立即返回 think 阶段在后台异步推进

当前 M1 阶段仅给出 router 占位 真实实现在 M2 阶段补齐
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["ask"])


@router.post("/ask")
async def ask() -> dict[str, str]:
    """创建任务并触发 think 阶段"""
    raise NotImplementedError("M2 实施")

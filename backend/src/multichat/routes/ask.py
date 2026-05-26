"""POST /ask 创建对话任务

请求体
    - session_id: 可选 不传则由 task_manager 内部新建会话
    - user_message: 用户问题文本 1~5000 字
响应
    - session_id: 任务所在的会话 id 用于侧边栏归类
    - task_id: 用于后续 SSE 订阅与 decide/cancel/retry 操作

设计说明
    - task_manager.create_task 当前签名只返回 task_id 没带 session_id
      为了节省一次往返 这里写入后再用 storage.get_round 反查 session_id 一并返回
    - 路由层不持有业务状态 创建后立刻把控制权交还给后台 think 协程
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class AskRequest(BaseModel):
    """提问请求 携带可选 session_id 与必填 user_message"""

    session_id: str | None = None
    user_message: str = Field(min_length=1, max_length=5000)
    # 本轮是否启用 thinking 模式  来自前端输入框的大脑开关  每次发送一次性透传
    # true 时后端会给 ChatOpenAI 注入 extra_body={"thinking":{"type":"enabled"}}
    # 让支持的模型走深度思考分支  不传或 false 时走原始路径
    thinking: bool = False


class AskResponse(BaseModel):
    """提问响应 返回 session_id 与新任务 task_id"""

    session_id: str
    task_id: str
    # ISO8601 字符串  来自 round.created_at  前端用它在用户气泡右侧显示创建时间
    # 不传给前端就只能 client 时间近似  会和数据库不一致
    created_at: str


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    """创建任务并触发 think 阶段 立刻返回 task_id"""
    tm = request.app.state.task_manager
    task_id = await tm.create_task(
        body.session_id, body.user_message, thinking_enabled=body.thinking
    )

    # task_manager.create_task 仅返回 task_id 反查一次 round 拿 session_id
    # 这一查是只读操作 不会阻塞后台 think 流程
    storage = request.app.state.storage
    round_obj = await storage.get_round(task_id)
    if round_obj is None:
        # 创建任务后立刻查不到 round 说明 task_manager 内部出了一致性问题
        raise HTTPException(500, "round not found after create")
    return AskResponse(
        session_id=round_obj.session_id,
        task_id=task_id,
        created_at=round_obj.created_at.isoformat(),
    )

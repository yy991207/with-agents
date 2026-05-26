"""POST /ask 创建对话任务

请求体
    - session_id: 可选 不传则由 task_manager 内部新建会话
    - user_message: 用户问题文本 1~5000 字
    - agents: 本轮发起的 agent name 列表  长度 1~4
    - input_mode: 'single' | 'multi'  对应输入框单/多 agent 切换
    - thinking: 大脑开关  对所有选中 agent 统一生效

响应
    - session_id: 任务所在的会话 id 用于侧边栏归类
    - task_id: 用于后续 SSE 订阅与 select_reply / retry_reply / cancel 操作
    - created_at: round.created_at ISO 字符串

设计说明
    - task_manager.create_task 当前签名只返回 task_id 没带 session_id
      为了节省一次往返 这里写入后再用 storage.get_round 反查 session_id 一并返回
    - 路由层不持有业务状态 创建后立刻把控制权交还给后台 reply 协程
    - 强校验:  同一 session 上一轮多 agent 模式且 selected_reply_agent 为空时
              直接返回 409  阻止用户在没选答前发下一轮  避免历史拼接歧义
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["chat"])


class AskRequest(BaseModel):
    """提问请求 携带可选 session_id 与必填 user_message"""

    session_id: str | None = None
    user_message: str = Field(min_length=1, max_length=5000)
    # 本轮发起的 agent name 列表  长度 1~4
    # single 模式 1 个  multi 模式 2~4 个
    agents: list[str] = Field(default_factory=list)
    # 输入模式  single 单 agent  multi 多 agent
    input_mode: Literal["single", "multi"] = "single"
    # 是否启用深度思考  对应输入框的大脑开关  本轮一次性
    # 后端据此给 ChatOpenAI 注入 extra_body={"thinking":{"type":"enabled"}}
    thinking: bool = False


class AskResponse(BaseModel):
    """提问响应 返回 session_id 与新任务 task_id"""

    session_id: str
    task_id: str
    # ISO8601 字符串  来自 round.created_at  前端用它在用户气泡右侧显示创建时间
    created_at: str


async def _ensure_last_round_selected(storage, session_id: str | None) -> None:
    """同一 session 内 上一轮如果是多 agent 且未选答  阻止开新轮

    单 agent 模式下后端 reply 完成会自动写 selected_reply_agent  不会触发本校验
    历史轮次 model_validator 已把 done 状态的旧 reply 自动迁移成 selected  也不会拦
    """
    from ..core.models import TaskState

    if not session_id:
        return
    rounds = await storage.list_rounds(session_id)
    if not rounds:
        return
    last = rounds[-1]
    # 取消 / 失败的轮次不参与选答校验  允许直接发下一轮
    state = last.state
    if state == TaskState.CANCELLED:
        return
    # 仅 DONE 且 multi 且 未选答时拦
    if state != TaskState.DONE:
        return
    if last.input_mode != "multi":
        return
    if last.selected_reply_agent:
        return
    raise HTTPException(
        status_code=409,
        detail="last_round_unselected: 请先在上一轮回答中选定一个 agent 才能开始新一轮",
    )


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    """创建任务并触发 reply 阶段 立刻返回 task_id"""
    # agents 字段校验
    if not body.agents:
        raise HTTPException(422, "agents 不能为空")
    if body.input_mode == "single" and len(body.agents) != 1:
        raise HTTPException(
            422,
            f"single 模式 agents 必须正好 1 个 实际 {len(body.agents)}",
        )
    if body.input_mode == "multi" and not (2 <= len(body.agents) <= 4):
        raise HTTPException(
            422,
            f"multi 模式 agents 必须 2~4 个 实际 {len(body.agents)}",
        )
    # 简单去重  避免用户多选了重复 agent
    if len(set(body.agents)) != len(body.agents):
        raise HTTPException(422, "agents 不能重复")

    storage = request.app.state.storage
    # 强校验:  上一轮多 agent 未选答 直接 409
    await _ensure_last_round_selected(storage, body.session_id)

    tm = request.app.state.task_manager
    try:
        task_id = await tm.create_task(
            body.session_id,
            body.user_message,
            agents=list(body.agents),
            input_mode=body.input_mode,
            thinking_enabled=body.thinking,
        )
    except ValueError as e:
        # 未知 agent / 模式校验失败 等  task_manager 内部 raise ValueError
        raise HTTPException(422, str(e))

    # task_manager.create_task 仅返回 task_id 反查一次 round 拿 session_id
    round_obj = await storage.get_round(task_id)
    if round_obj is None:
        raise HTTPException(500, "round not found after create")
    return AskResponse(
        session_id=round_obj.session_id,
        task_id=task_id,
        created_at=round_obj.created_at.isoformat(),
    )

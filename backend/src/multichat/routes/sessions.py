"""GET /sessions 列出最近 N 个 session 用于侧边栏

无路径参数 query 可选 limit 控制条数 默认 50

DELETE /sessions/{session_id} 删除 session 与其下所有 rounds
    - 204 删除成功
    - 404 session 不存在
    - 409 session 下还有进行中 round 不允许删除

POST /sessions/batch-delete 批量删除会话
    请求体 { session_ids: string[] }
    - 200 { deleted, skipped, errors[] } 逐条返回结果，不因单条失败而全回滚

POST /api/sessions/{session_id}/compact 同步触发会话上下文压缩
    无请求体  路由在调用 LLM 摘要期间一直阻塞 直到拿到结果
    - 200 { summary, summary_until_round, summary_updated_at, used_tokens_before,
           used_tokens_after, max_input_tokens, model_id }
    - 404 session 不存在
    - 409 session 下还有进行中 round
    - 422 没有可压缩的 round
    - 503 LLM 调用失败 / 找不到可用 agent
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth_context import get_current_identity
from ..core.models import RequestIdentity
from ..core.errors import humanize_llm_error
from ..llm.summarization import run_session_summary
from ..llm.token_counter import count_history_tokens, usage_payload

router = APIRouter(prefix="", tags=["history"])

_logger = structlog.get_logger(__name__)

# 进行中状态  与 storage.delete_session 内的 in_progress 列表对齐
_IN_PROGRESS_STATES = {
    "pending",
    "thinking",
    "think_done",
    "decided",
    "replying",
    # 历史值兼容
    "created",
    "waiting_decision",
}

# compact 路由内部使用 单条 LLM 摘要超时上限  与 task_manager 自动压缩对齐
# 用户手动触发可能上下文很长  60s 容易超时  故走 120s
_COMPACT_LLM_TIMEOUT_S: float = 120.0

# agent.available_models 中找不到当前 model_id 对应 max_input_tokens 时的兜底
# 与 task_manager._maybe_auto_compact 保持一致  避免两边阈值口径不同
_DEFAULT_MAX_INPUT_TOKENS: int = 200000


class BatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=200)


class BatchDeleteResult(BaseModel):
    deleted: int
    skipped: int
    errors: list[str]


class CompactResponse(BaseModel):
    """POST /sessions/{id}/compact 响应  字段全部 snake_case 与前端 ContextUsage 对齐"""

    summary: str
    summary_until_round: int
    summary_updated_at: str
    used_tokens_before: int
    used_tokens_after: int
    max_input_tokens: int
    model_id: str


class BranchSessionRequest(BaseModel):
    source_task_id: str = Field(min_length=1)
    source_role: Literal["user", "assistant"]
    source_agent: str | None = None


class BranchSessionResponse(BaseModel):
    session_id: str
    draft_message: str | None = None


@router.get("/sessions")
async def list_sessions(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """列出最近会话 给前端左侧栏初始化用 按 updated_at 降序"""
    storage = request.app.state.storage
    metas = await storage.list_sessions(
        tenant_id=identity.tenant_id,
        owner_user_id=identity.user_id,
        limit=limit,
    )
    return [m.model_dump(mode="json") for m in metas]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> None:
    """删除指定 session 及其下所有 rounds

    错误码:
        404 session 不存在
        409 session 下还有进行中的 round 提示用户先取消或等其完成
    """
    storage = request.app.state.storage
    try:
        await storage.delete_session(
            session_id,
            tenant_id=identity.tenant_id,
            owner_user_id=identity.user_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    except ValueError as e:
        # 进行中 round 阻塞删除 返回 409 让前端提示用户
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/sessions/batch-delete", response_model=BatchDeleteResult)
async def batch_delete_sessions(
    body: BatchDeleteRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> BatchDeleteResult:
    """批量删除会话 逐条执行，单条失败不阻塞其他条"""
    storage = request.app.state.storage
    deleted = 0
    skipped = 0
    errors: list[str] = []
    for sid in body.session_ids:
        try:
            await storage.delete_session(
                sid,
                tenant_id=identity.tenant_id,
                owner_user_id=identity.user_id,
            )
            deleted += 1
        except KeyError:
            # session 不存在，跳过
            skipped += 1
        except ValueError as e:
            # 进行中的 round 阻塞删除
            errors.append(f"{sid}: {e}")
            skipped += 1
    return BatchDeleteResult(deleted=deleted, skipped=skipped, errors=errors)


@router.post(
    "/sessions/{session_id}/branch",
    response_model=BranchSessionResponse,
)
async def branch_session(
    session_id: str,
    body: BranchSessionRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> BranchSessionResponse:
    """基于当前会话某个 user / assistant 节点复制一份前缀历史生成子会话

    行为:
        - user 分支: 复制该轮之前的历史 当前 user 文本作为 draft_message 返回给前端预填
        - assistant 分支: 复制该轮及其选中的 assistant 回复 draft_message 为空
        - 若原会话前缀可安全复用摘要则一并复制 否则清空摘要避免未来信息泄漏
    """
    storage = request.app.state.storage
    try:
        new_session_id, draft_message = await storage.clone_session_branch(
            source_session_id=session_id,
            tenant_id=identity.tenant_id,
            owner_user_id=identity.user_id,
            source_task_id=body.source_task_id,
            source_role=body.source_role,
            source_agent=body.source_agent,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return BranchSessionResponse(session_id=new_session_id, draft_message=draft_message)


@router.post(
    "/sessions/{session_id}/compact",
    response_model=CompactResponse,
)
async def compact_session(
    session_id: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> CompactResponse:
    """同步触发会话压缩  路由层串行调用 LLM 拿到摘要后写回 mongo

    路径说明:
        sessions router prefix="" 所以这里写 /sessions/.../compact
        前端 http.ts 应配套调用 /sessions/{id}/compact (不带 /api 前缀  与 list/delete 一致)

    流程:
        1 取 session 不存在 -> 404
        2 list_rounds 检查进行中 round -> 409
        3 过滤 reply.state == "done" 的 round  没有则 422
        4 选摘要 agent  优先最近一轮 reply.agent  fallback judge 指针  都没有抛 503
        5 取 agent.available_models 中当前 model_id 的 max_input_tokens  缺省走兜底
        6 拼 history 估算 used_tokens_before
        7 await run_session_summary  超时 120s
        8 storage.update_session_summary 写回 summary 与 summary_until_round
        9 用 [{"role":"system","content":new_summary}] 估算 used_tokens_after
       10 返回结构化响应

    错误响应:
        404 session 不存在
        409 进行中 round 阻塞压缩  detail 提示哪一类 state
        422 没有可压缩 round  message: "no completed rounds to summarize"
        503 LLM 调用失败 或 找不到可用摘要 agent  detail 走 humanize_llm_error
    """
    storage = request.app.state.storage

    # 1 拿 session
    session = await storage.get_session(
        session_id,
        tenant_id=identity.tenant_id,
        owner_user_id=identity.user_id,
    )
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"session not found: {session_id}"
        )

    # 2 拿 rounds 检查进行中状态
    all_rounds = await storage.list_rounds(session_id)
    in_progress = [
        r for r in all_rounds if getattr(r.state, "value", str(r.state)) in _IN_PROGRESS_STATES
    ]
    if in_progress:
        raise HTTPException(
            status_code=409,
            detail=(
                f"session has {len(in_progress)} in-progress round(s) "
                "请先取消或等待这些任务完成再触发压缩"
            ),
        )

    # 3 过滤已完成 round  reply.state == "done" 才算可摘要素材
    done_rounds = [
        r for r in all_rounds if r.reply and r.reply.get("state") == "done"
    ]
    if not done_rounds:
        raise HTTPException(
            status_code=422, detail="no completed rounds to summarize"
        )

    # 4 选摘要 agent  优先最近一轮 reply.agent
    agent_name: str | None = None
    last_reply = done_rounds[-1].reply or {}
    last_agent = last_reply.get("agent")
    if isinstance(last_agent, str) and last_agent:
        cand = await storage.get_agent(last_agent)
        if cand is not None:
            agent_name = last_agent

    # fallback 到 judge 指针
    if agent_name is None:
        try:
            judge = await storage.get_judge_target()
        except KeyError:
            judge = ""
        if judge:
            cand = await storage.get_agent(judge)
            if cand is not None:
                agent_name = judge

    if agent_name is None:
        # 无可用 agent  按 503 抛  detail 给中文提示
        raise HTTPException(
            status_code=503, detail="no agent available for summarization"
        )

    record = await storage.get_agent(agent_name)
    if record is None:
        # 拿到 name 但 agent 已被并发删除  极少见  保险起见再判一次
        raise HTTPException(
            status_code=503, detail="no agent available for summarization"
        )

    # 5 取 max_input_tokens  与 task_manager 口径保持一致  缺省走兜底
    max_input_tokens = next(
        (
            m.max_input_tokens
            for m in record.available_models
            if m.model_id == record.model
        ),
        _DEFAULT_MAX_INPUT_TOKENS,
    )

    # 6 拼 history  与 _build_history 口径保持一致
    #     question -> user message
    #     reply.content -> assistant message
    history: list[dict[str, Any]] = []
    for r in done_rounds:
        history.append({"role": "user", "content": r.question})
        history.append(
            {
                "role": "assistant",
                "content": (r.reply or {}).get("content", "") or "",
            }
        )

    used_tokens_before = int(count_history_tokens(history))

    # 7 调 LLM 摘要  任何异常一律按 503 抛  detail 走 humanize_llm_error
    old_summary = session.summary or ""
    try:
        new_summary = await run_session_summary(
            history=history,
            old_summary=old_summary,
            agent_record=record,
            timeout_s=_COMPACT_LLM_TIMEOUT_S,
        )
    except HTTPException:
        # 上面的逻辑里不会主动抛  保险起见保留原样上抛
        raise
    except Exception as e:  # noqa: BLE001
        # 把底层异常转成中文短提示  避免泄露完整堆栈与敏感 url
        _logger.exception(
            "compact_session run_session_summary 失败",
            session_id=session_id,
            agent=agent_name,
        )
        raise HTTPException(status_code=503, detail=humanize_llm_error(e))

    # 8 写回 mongo  覆盖式更新  summary_until_round 取最后一条 done round
    summary_until_round = int(done_rounds[-1].round_index)
    try:
        await storage.update_session_summary(
            session_id,
            summary=new_summary,
            summary_until_round=summary_until_round,
        )
    except KeyError:
        # 极少见  写回时 session 已被并发删掉
        raise HTTPException(
            status_code=404, detail=f"session not found: {session_id}"
        )

    # 9 估算压缩后 token  用模拟下次 _build_history 注入摘要后的形态
    used_tokens_after = int(
        count_history_tokens([{"role": "system", "content": new_summary}])
    )

    # 9.5 同步把 context_usage 快照写回 sessions  让前端刷新 / 切会话后能恢复进度条
    # 写失败仅 log 不阻塞响应  反正下一轮 reply 还会再写一次
    try:
        post_payload = usage_payload(
            used_tokens_after, int(max_input_tokens), model_id=record.model
        )
        await storage.update_session_context_usage(session_id, post_payload)
    except Exception:
        _logger.exception(
            "compact 后写 context_usage 失败 忽略", session_id=session_id
        )

    # 10 取最新 summary_updated_at  以 mongo 写入后回查为准  减少时区误差
    refreshed = await storage.get_session(session_id)
    if refreshed and refreshed.summary_updated_at is not None:
        ts = refreshed.summary_updated_at.isoformat()
    else:
        # 兜底  极端情况下回查为空  用当前 utc 时间  保证字段可序列化
        ts = datetime.now(timezone.utc).isoformat()

    _logger.info(
        "compact_session 完成",
        session_id=session_id,
        agent=agent_name,
        model_id=record.model,
        before=used_tokens_before,
        after=used_tokens_after,
        summary_until_round=summary_until_round,
    )

    return CompactResponse(
        summary=new_summary,
        summary_until_round=summary_until_round,
        summary_updated_at=ts,
        used_tokens_before=used_tokens_before,
        used_tokens_after=used_tokens_after,
        max_input_tokens=int(max_input_tokens),
        model_id=record.model,
    )

"""真实 LLM 调用封装

把 deepagents 流式事件桥接到 TaskManager 的 TaskEvent 流
think 阶段非流式拿到完整 50 字理由 reply 阶段流式吐 token
judge 阶段在 4 个 think agent 中选一个跑 ainvoke 走非流式

约束:
    - 全部以协程形式实现 配合 asyncio.gather 并发触发
    - 通过外部传入的 on_event 回调把中间事件推到 TaskEventHub 不在本模块持有 hub 引用
    - 异常向上抛 由 TaskManager 捕获并落到 think.failed / reply.error 事件
    - 异步 LLM 客户端在 deep_agents 模块创建 这里只持引用 不会跨 loop 复用对象
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import structlog
from langchain_core.messages import AIMessage, HumanMessage

from ..core.events import TaskEvent
from .deep_agents import DeepAgentRegistry

_logger = structlog.get_logger(__name__)

# on_event 回调签名 接收 TaskEvent 返回协程
EventCallback = Callable[[TaskEvent], Awaitable[None]]


# ============================================================================ Think
async def run_think(
    agent_name: str,
    user_message: str,
    history: list[dict[str, Any]],
    registry: DeepAgentRegistry,
    timeout_s: float,
) -> str:
    """运行某 agent 的 think 调用 返回最终发言理由(50 字以内)

    history 由调用方裁剪好 [{"role":"user|assistant","content":"...","agent":"..."}]
    超时直接抛 asyncio.TimeoutError 由调用方 try/except 标 failed
    """
    deep_agent = registry.get(agent_name, "think")
    messages = _build_messages(history, user_message)
    state = await asyncio.wait_for(
        deep_agent.ainvoke({"messages": messages}, config={"recursion_limit": 1000}),
        timeout=timeout_s,
    )
    text = _extract_final_ai_text(state)
    return text.strip()


# ============================================================================ Reply
async def run_reply(
    agent_name: str,
    user_message: str,
    history: list[dict[str, Any]],
    registry: DeepAgentRegistry,
    on_event: EventCallback,
    timeout_s: float,
) -> str:
    """运行被选中 agent 的 reply 流式调用

    通过 on_event 回调把 token chunk 与 tool 调用推出去
    返回最终完整 reply 文本(由 chunk 拼接)
    超时直接抛 asyncio.TimeoutError
    """
    deep_agent = registry.get(agent_name, "reply")
    messages = _build_messages(history, user_message)
    full_text_parts: list[str] = []

    async def _stream() -> None:
        # deepagents 0.6.x astream_events v2 协议 字段细节见 verify_deepagents.py
        async for ev in deep_agent.astream_events(
            {"messages": messages},
            version="v2",
            config={"recursion_limit": 1000},
        ):
            event_type = ev.get("event")
            if event_type == "on_chat_model_stream":
                chunk = ev.get("data", {}).get("chunk")
                text = _chunk_to_text(chunk)
                if text:
                    full_text_parts.append(text)
                    await on_event(
                        TaskEvent(
                            type="reply.chunk",
                            data={"agent": agent_name, "chunk": text},
                        )
                    )
            elif event_type == "on_tool_start":
                tool_name = ev.get("name", "")
                tool_input = ev.get("data", {}).get("input", {})
                await on_event(
                    TaskEvent(
                        type="reply.tool_call",
                        data={
                            "agent": agent_name,
                            "tool": tool_name,
                            "input": _truncate_repr(tool_input),
                        },
                    )
                )
            elif event_type == "on_tool_end":
                tool_name = ev.get("name", "")
                output = ev.get("data", {}).get("output", "")
                await on_event(
                    TaskEvent(
                        type="reply.tool_result",
                        data={
                            "agent": agent_name,
                            "tool": tool_name,
                            "result": _truncate_repr(output, 300),
                        },
                    )
                )

    await asyncio.wait_for(_stream(), timeout=timeout_s)
    return "".join(full_text_parts)


# ============================================================================ Judge
async def run_judge(
    judge_agent_name: str,
    user_message: str,
    thinks: dict[str, str],
    registry: DeepAgentRegistry,
    timeout_s: float,
) -> str:
    """让 judge agent 选出最适合回答的 agent

    参数:
        judge_agent_name: 担任裁判的 agent 名 复用其 think 实例(强约束 不调工具)
        user_message: 用户原始问题
        thinks: 已成功的 think 内容 形如 {"GLM": "...", "Kimi": "..."}
        registry: deep_agent 注册表
        timeout_s: 单次 ainvoke 超时

    返回:
        被选中的 agent 名 必须是 thinks.keys() 之一
        若 LLM 输出无法识别 降级返回 thinks 中的第一个 key
    """
    if not thinks:
        raise RuntimeError("run_judge 入参 thinks 为空 至少需要 1 个候选")

    deep_agent = registry.get(judge_agent_name, "think")
    options = list(thinks.keys())
    prompt_lines = [
        f"用户提问 {user_message}",
        "",
        "下面是 4 个 AI 助手对该问题给出的 50 字以内发言意愿",
    ]
    for name, reason in thinks.items():
        prompt_lines.append(f"[{name}] {reason}")
    prompt_lines.append("")
    prompt_lines.append(
        f"请只输出最适合回答的助手名字 必须从 {options} 中选 不要解释 不要添加多余字符"
    )
    prompt = "\n".join(prompt_lines)

    state = await asyncio.wait_for(
        deep_agent.ainvoke({"messages": [HumanMessage(content=prompt)]}),
        timeout=timeout_s,
    )
    text = _extract_final_ai_text(state).strip()

    # 模糊匹配 LLM 输出 找第一个出现的候选 大小写不敏感
    text_lower = text.lower()
    for name in options:
        if name.lower() in text_lower:
            return name

    _logger.warning(
        "judge 输出不合规 降级到第一选项",
        judge=judge_agent_name,
        raw=text[:80],
        options=options,
    )
    return options[0]


# ============================================================================ Helpers
def _build_messages(history: list[dict[str, Any]], user_message: str) -> list[Any]:
    """从 history 拼 langchain messages 调用方负责裁剪到 history_max_rounds

    history 由 task_manager 传入 已含历史 user_message 与 reply.content
    支持的角色 user / assistant 其他角色一律忽略
    """
    out: list[Any] = []
    for h in history:
        role = h.get("role")
        content = h.get("content", "")
        if not isinstance(content, str):
            continue
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    out.append(HumanMessage(content=user_message))
    return out


def _extract_final_ai_text(state: Any) -> str:
    """从 deepagents 返回的 state 中取最后一条 ai/assistant 文本

    deepagents 0.6.x ainvoke 返回的 state 是 dict 含 messages list
    最后一条若非 ai 消息则继续往前找 取第一条带文本的 ai 消息
    """
    msgs = state.get("messages", []) if isinstance(state, dict) else []
    for m in reversed(msgs):
        kind = getattr(m, "type", None) or getattr(m, "role", None)
        content = getattr(m, "content", None)
        if kind not in ("ai", "assistant"):
            continue
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text_parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            joined = "\n".join(p for p in text_parts if p)
            if joined.strip():
                return joined
    return ""


def _chunk_to_text(chunk: Any) -> str:
    """从 AIMessageChunk 中取出可打印文本

    chunk.content 可能是 str 也可能是 list[dict] 取决于底层模型协议
    list 形式只取 type==text 的 text 字段拼接
    """
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


def _truncate_repr(obj: Any, n: int = 200) -> str:
    """安全 repr 并截断 防止超长 tool_call 输入污染日志"""
    s = repr(obj)
    if len(s) <= n:
        return s
    return s[:n] + "..."

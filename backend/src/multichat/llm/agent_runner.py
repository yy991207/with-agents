"""真实 LLM 调用封装

把 deepagents 流式事件桥接到 TaskManager 的 TaskEvent 流
reply 阶段流式吐 token 支持深度思考(reasoning)模式

约束:
    - 全部以协程形式实现 配合 asyncio.gather 并发触发
    - 通过外部传入的 on_event 回调把中间事件推到 TaskEventHub 不在本模块持有 hub 引用
    - 异常向上抛 由 TaskManager 捕获并落到 reply.error 事件
    - 异步 LLM 客户端在 deep_agents 模块创建 这里只持引用 不会跨 loop 复用对象
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.events import TaskEvent
from .deep_agents import DeepAgentRegistry

_logger = structlog.get_logger(__name__)

# on_event 回调签名 接收 TaskEvent 返回协程
EventCallback = Callable[[TaskEvent], Awaitable[None]]


# ============================================================ tool 序列化辅助
# 工具事件给前端的字符串必须是真实可读形式  原本一律走 repr() 会得到
# {'url': 'https://x'} 这种 Python 字面量 前端无法 JSON.parse 也丑
# 所以这里把 input 走 JSON 序列化  output 走 str()  保留兜底走 repr
def _json_dump_safe(obj: Any) -> str:
    """把任意对象尽量序列化成 JSON  失败回退到 str / repr  绝不抛"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        try:
            return str(obj)
        except Exception:
            return repr(obj)


def _stringify_tool_output(obj: Any) -> str:
    """工具结果转字符串  langchain ToolMessage 的 __str__ 返回 content
    其它对象用 str() 即可  None 给空串
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    # ToolMessage 等 langchain 消息对象 优先取 content 字段
    content = getattr(obj, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 多模态 content 取 type==text 的部分拼起来 与 _chunk_to_text 对齐
        try:
            return "".join(
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        except Exception:
            return str(obj)
    try:
        return str(obj)
    except Exception:
        return repr(obj)


# ============================================================================ Reply
async def run_reply(
    agent_name: str,
    user_message: str,
    history: list[dict[str, Any]],
    registry: DeepAgentRegistry,
    on_event: EventCallback,
    thinking_enabled: bool = False,
    owner_user_id: str | None = None,
) -> str:
    """运行被选中 agent 的 reply 流式调用

    owner_user_id 用于按用户获取 agent 实例 (含 MCP/Skills)
    """
    if thinking_enabled:
        deep_agent = await registry.build_thinking_reply(agent_name, owner_user_id=owner_user_id)
    else:
        deep_agent = await registry.get_or_build(agent_name, owner_user_id=owner_user_id)
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
                # 先看 reasoning_content (DeepSeek-R1 / GLM-4.5 / 部分 OpenAI 兼容协议)
                # langchain 把它放在 chunk.additional_kwargs.reasoning_content
                # 与 content 分离  必须单独取  否则前端永远看不到 reasoning
                reasoning_text = _chunk_to_reasoning(chunk)
                if reasoning_text:
                    await on_event(
                        TaskEvent(
                            type="reply.thinking",
                            data={"agent": agent_name, "chunk": reasoning_text},
                        )
                    )
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
                # 入参用 JSON 序列化  前端能直接 JSON.parse 渲染语法高亮
                # 失败回退 str / repr  保证不抛
                await on_event(
                    TaskEvent(
                        type="reply.tool_call",
                        data={
                            "agent": agent_name,
                            "tool": tool_name,
                            "input": _json_dump_safe(tool_input),
                        },
                    )
                )
            elif event_type == "on_tool_end":
                tool_name = ev.get("name", "")
                output = ev.get("data", {}).get("output", "")
                # 结果取真实文本  ToolMessage 这类对象会拿 content 字段
                # 不再 repr + 截断 300  前端有滚动 + 折叠
                await on_event(
                    TaskEvent(
                        type="reply.tool_result",
                        data={
                            "agent": agent_name,
                            "tool": tool_name,
                            "result": _stringify_tool_output(output),
                        },
                    )
                )

    await _stream()
    return "".join(full_text_parts)


# ============================================================================ Helpers
def _build_messages(history: list[dict[str, Any]], user_message: str) -> list[Any]:
    """从 history 拼 langchain messages 调用方负责裁剪到 history_max_rounds

    history 由 task_manager 传入 已含历史 user_message 与 reply.content
    支持的角色:
        - system: 通常是 _build_history 注入的会话摘要  转 SystemMessage 排在最前
        - user: 用户提问 转 HumanMessage
        - assistant: 上一轮 reply 转 AIMessage
        - 其它角色一律忽略
    """
    out: list[Any] = []
    for h in history:
        role = h.get("role")
        content = h.get("content", "")
        if not isinstance(content, str):
            continue
        if role == "system":
            # 摘要注入  排序由 history 列表本身决定 调用方应将摘要放在首位
            out.append(SystemMessage(content=content))
        elif role == "user":
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


def _chunk_to_reasoning(chunk: Any) -> str:
    """从 AIMessageChunk 中取出 reasoning_content (深度思考内容)

    DeepSeek-R1 / GLM-4.5 等模型把 reasoning 内容放在 additional_kwargs.reasoning_content
    与正常 content 分离  langchain 不会自动拼到 chunk.content 里
    所以必须单独取  否则前端永远看不到 reasoning

    兼容 OpenAI o-series 的 reasoning  也可能放在 additional_kwargs.reasoning
    都试一下  哪个有取哪个  都没有返回空串
    """
    if chunk is None:
        return ""
    extras = getattr(chunk, "additional_kwargs", None)
    if not isinstance(extras, dict):
        return ""
    # 优先 reasoning_content (DeepSeek-R1 / GLM)  其次 reasoning (个别厂商)
    for key in ("reasoning_content", "reasoning"):
        v = extras.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _truncate_repr(obj: Any, n: int = 200) -> str:
    """安全 repr 并截断 防止超长 tool_call 输入污染日志"""
    s = repr(obj)
    if len(s) <= n:
        return s
    return s[:n] + "..."

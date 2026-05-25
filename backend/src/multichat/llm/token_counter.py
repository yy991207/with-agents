"""token 计数与摘要触发阈值工具

设计要点:
    - 不发网络请求 纯本地估计  避免每轮判断都打 LLM
    - 用 langchain 自带 count_tokens_approximately  英文 4 char/token  中文偏保守
    - 阈值固定 80%  来自原始需求"模型最大窗口的 80%"
      改这个比例需要同步改前端进度条的颜色阈值

调用关系:
    task_manager 每轮 reply 完成后:
        used = count_history_tokens(messages, tools=...)
        threshold = compute_summary_threshold(max_input_tokens)
        if should_trigger_summary(used, max_input_tokens):
            -> 触发摘要 / 推 SSE context.usage 让前端展示
"""

from __future__ import annotations

from typing import Any, Iterable

from langchain_core.messages.utils import count_tokens_approximately

# ============================================================================
# 触发比例  会话总 token >= max_input_tokens × 此值时触发摘要
# 改这里需要同步改前端  web/src/components/ChatInput.tsx 进度条警示色阈值
# ============================================================================
SUMMARY_TRIGGER_RATIO: float = 0.8


def count_history_tokens(
    messages: Iterable[Any],
    *,
    tools: list[Any] | None = None,
) -> int:
    """估算消息列表的总 token 数  含 tools schema 占用

    参数:
        messages: langchain 消息列表  HumanMessage / AIMessage / SystemMessage 等
            也接受 dict 形式如 {"role":"user","content":"..."}
            count_tokens_approximately 会自己做归一化
        tools: 工具列表  BaseTool 实例或 dict schema 都行
            None 表示当前调用没挂工具

    返回:
        估算 token 数  4 char ≈ 1 token  对中文偏保守(中文实际 ~2 char/token)
        故估算结果通常比真实大几个百分点  用作触发判断时不会漏触发
    """
    return count_tokens_approximately(messages, tools=tools)


def compute_summary_threshold(max_input_tokens: int) -> int:
    """根据模型最大输入窗口计算摘要触发阈值  返 max × 80% 取整

    max_input_tokens <= 0 时直接抛 ValueError  防止上游传错值导致总是触发或永不触发
    """
    if max_input_tokens <= 0:
        raise ValueError(
            f"max_input_tokens 必须 >0  实际 {max_input_tokens}"
        )
    return int(max_input_tokens * SUMMARY_TRIGGER_RATIO)


def should_trigger_summary(used_tokens: int, max_input_tokens: int) -> bool:
    """判断当前 token 用量是否达到摘要触发阈值

    used >= max × 80%  返回 True
    used 取负值或 max 非法时直接 False  避免误触发
    """
    if used_tokens < 0 or max_input_tokens <= 0:
        return False
    return used_tokens >= compute_summary_threshold(max_input_tokens)


def usage_payload(
    used_tokens: int, max_input_tokens: int, *, model_id: str = ""
) -> dict[str, Any]:
    """构造 SSE context.usage 事件 payload  前端进度条直接消费

    字段:
        used_tokens: 当前会话所有历史的估算 token 数
        threshold_tokens: 触发摘要的阈值  = max × 80%
        max_input_tokens: 模型最大输入窗口
        ratio: used / max  保留 4 位小数  前端方便画条
        model_id: 当前 reply agent 选用的 model_id  便于前端展示
    """
    safe_max = max_input_tokens if max_input_tokens > 0 else 1
    return {
        "used_tokens": int(used_tokens),
        "threshold_tokens": compute_summary_threshold(max_input_tokens)
        if max_input_tokens > 0
        else 0,
        "max_input_tokens": int(max_input_tokens),
        "ratio": round(used_tokens / safe_max, 4),
        "model_id": model_id,
    }

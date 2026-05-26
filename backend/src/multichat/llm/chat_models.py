"""ChatOpenAI 子类  补丁第三方 provider 的非标字段

langchain_openai 1.2.x 的 ChatOpenAI 显式说明:
    Non-standard response fields added by third-party providers
    (e.g. reasoning_content reasoning_details) are not extracted or preserved.

我们用阿里百炼 / DashScope 的 GLM-5.1 / Qwen3 等模型时
delta 里的 reasoning_content 会被官方丢弃  导致 thinking 内容前端永远看不到

这里通过子类重写 _convert_chunk_to_generation_chunk
在父类返回 ChatGenerationChunk 之后  从原始 chunk 的 choices[0].delta 里
把 reasoning_content 单独取出来  注入到 message.additional_kwargs.reasoning_content
让上游 agent_runner._chunk_to_reasoning 能拿到
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    """带 reasoning_content 透传的 ChatOpenAI

    适配阿里百炼 / DashScope / DeepSeek 等 OpenAI 兼容协议的"伪标准"字段
    其它行为完全继承父类  仅在 chunk 转换时多走一步注入逻辑

    设计取舍:
        - 不重写 _generate / _stream  那是同步实现  我们只关心 _astream
        - _convert_chunk_to_generation_chunk 是 _stream / _astream 共用入口
          重写一次两边都拿到 reasoning_content
        - 父类返回 None 时直接透传  保留原有"空 delta 不发 chunk"逻辑
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None:
            return None

        # 取原始 delta 里 reasoning_content 字段  父类 _convert_delta_to_message_chunk 没接
        # choices 路径与父类一致  兼容 beta.chat.completions.stream 形状
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if not choices:
            return result
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning_content")
        if not isinstance(reasoning, str) or not reasoning:
            return result

        msg = result.message
        if isinstance(msg, AIMessageChunk):
            # additional_kwargs 不一定是 None  父类初始化为 {} 时直接 setdefault
            if not msg.additional_kwargs:
                msg.additional_kwargs = {}
            # 多个 chunk 之间不需要拼接  调用方会按 chunk 顺序累积
            msg.additional_kwargs["reasoning_content"] = reasoning
        return result

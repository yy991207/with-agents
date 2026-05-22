"""把底层 LLM 调用异常映射成对用户友好的中文提示

设计目标:
    - 屏蔽完整堆栈与敏感 url 仅返回简短中文提示
    - 命中常见错误模式时给出具体语义 让前端能对症提示
    - 兜底兼容 仅截断到约 80 字 避免泄露过多内部细节

仅做"展示层"提示 不影响具体异常的捕获或上抛逻辑
"""

from __future__ import annotations

import re

# (regex, 中文提示) 顺序从高优先级 → 低优先级
# 命中即返回 后续 pattern 不再匹配
# 注意: 一些常见关键字组合在英文堆栈中出现频率高 优先匹配它们
_FRIENDLY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"timeout|TimeoutException", re.I), "请求超时 LLM 服务响应慢或无响应"),
    (re.compile(r"\b429\b|rate.?limit|too\s*many", re.I), "调用频率过高 已被限流 请稍后再试"),
    (re.compile(r"\b401\b|unauthor|invalid.?api.?key", re.I), "API key 无效或已过期"),
    (re.compile(r"\b403\b|forbidden", re.I), "无访问权限 请检查凭证"),
    (re.compile(r"context.?length|too.?many.?tokens", re.I), "对话上下文过长 请新建会话或缩短输入"),
    (re.compile(r"\b5\d\d\b|server\s*error|service\s*unavailable", re.I), "LLM 服务暂时不可用"),
    (re.compile(r"connection|network|dns", re.I), "网络连接失败 请检查网络或稍后重试"),
    (re.compile(r"cancel", re.I), "任务已取消"),
    (re.compile(r"json|parse|decode", re.I), "LLM 返回内容解析失败"),
]

# 兜底截断长度 防止把巨大 traceback 直接抛回前端
_FALLBACK_MAX_LEN = 80


def humanize_llm_error(err: BaseException | str) -> str:
    """把 LLM 调用产生的异常转成中文用户提示

    入参可以是异常对象 或已经 str 化的错误字符串 都会先拼成 "类型名: 文本"
    再依次尝试匹配 _FRIENDLY_PATTERNS 中的正则
    匹配不到的兜底返回截断到 _FALLBACK_MAX_LEN 长度 避免泄露完整堆栈
    """
    if isinstance(err, BaseException):
        text = f"{type(err).__name__}: {err}"
    else:
        text = str(err)

    for pat, msg in _FRIENDLY_PATTERNS:
        if pat.search(text):
            return msg

    # 兜底 防止用户看到完整堆栈或敏感 url
    short = text[:_FALLBACK_MAX_LEN]
    return f"调用失败 {short}"

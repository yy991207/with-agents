"""会话摘要模块  把多轮对话压缩成结构化摘要 写回 sessions.summary

调用关系:
    task_manager.maybe_compact_session(session_id) ->
        run_session_summary(...) -> 返回新摘要文本 ->
        storage.update_session_summary(...) 写回 mongo

模板来源:
    Prompt 字面值与项目根目录 doc/会话摘要 Prompt 模板.md "二、模板正文" 完全同步
    模板改动需要两边一起改  PR diff 里能直接看到差异
    不在运行时读 doc 文件 避免线上副本不一致

字段约定:
    - 章节固定 7 段中文 不增减不换序
    - 总长度 ≤ 2000 字
    - 整合上一版摘要 不简单拼接
    - 保留专有名词 / 文件路径 / 错误码 / 链接 / 版本号 原文不翻译
"""

from __future__ import annotations

# ============================================================================
# Prompt 模板常量
# ----------------------------------------------------------------------------
# 与 doc/会话摘要 Prompt 模板.md 中 "二、模板正文" 严格一致
# 字数上限 2026-05-25 起从 1500 调整为 2000  改这里同步改 doc
# ============================================================================
SESSION_SUMMARY_SYSTEM_PROMPT = """你是一个会话摘要助手。你的任务是把一段多轮对话压缩成一份结构化的会话摘要,供后续对话继续使用。

## 强制要求

1. 严格按照下面给定的章节结构输出,不要增减章节,不要改名,不要换顺序。
2. 每个章节内部用简洁中文分点描述,每点尽量不超过两行。
3. 不要复述原对话内容,而是抽取关键事实、决策、状态、待办。
4. 如果某个章节没有可写的内容,保留章节标题,内容写"无"。
5. 保留所有专有名词、文件路径、函数名、错误码、链接、版本号的原文,不要翻译,不要改写。
6. 不要输出"以下是摘要""根据对话"之类的引导语,直接从第一个章节标题开始。
7. 总长度不超过 2000 字。如果原对话已经包含「上一版摘要」,在新摘要里整合它,不要简单拼接。

## 输出章节结构

### 1. 会话目标
用户在这段对话里想达成的核心目的(一句话)。如果有多个目的,按优先级列出。

### 2. 用户身份与偏好
从对话里能推断出来的用户角色、技术栈背景、沟通偏好、明确禁止事项。仅写"对话里出现过的",不要编。

### 3. 关键决策与共识
对话过程中已经拍板的方案、放弃的方案、达成共识的取舍。每条注明"决定: XXX, 原因: XXX"。

### 4. 当前进展
对话进行到什么程度:已经完成了什么、正在做什么、阻塞在哪里。用过去式/进行时,不要用将来时。

### 5. 重要上下文事实
对话里出现的、对后续工作必须保留的事实信息。例如:
- 文件路径与对应作用
- 接口名称、字段、错误码
- 第三方库版本、配置项、环境差异
- 关键日志、报错原文(只摘录关键行)

### 6. 待办与下一步
还没做完、需要后续接着做的事项,按优先级列出,每条形如"待办: XXX, 负责方: 用户/助手"。

### 7. 风险与坑
对话中暴露过、但还没解决的潜在风险、容易踩的坑、需要人工二次确认的点。
"""


# ============================================================================
# Human 消息拼装模板
# ----------------------------------------------------------------------------
# 调用方式参考 doc/会话摘要 Prompt 模板.md "一、使用方式":
#     SystemMessage(SESSION_SUMMARY_SYSTEM_PROMPT)
#     HumanMessage(SESSION_SUMMARY_HUMAN_TEMPLATE.format(
#         old_summary=old or "(无)",
#         conversation=rendered_history,
#     ))
# 待压缩对话以"用户: ...\n助手: ..."的纯文本形式贴进来 不带元信息
# ============================================================================
SESSION_SUMMARY_HUMAN_TEMPLATE = """[上一版摘要]
{old_summary}

[待压缩对话]
{conversation}
"""


# ============================================================================
# 摘要长度软上限  仅供前后端判断 LLM 是否超规
# ----------------------------------------------------------------------------
# 与 prompt 第 7 条字面值保持一致  改这里也得改 prompt
# 用途:
#   1 判断摘要是否需要二次压缩
#   2 前端展示摘要时给个"已用 / 上限"提示
# ============================================================================
SESSION_SUMMARY_MAX_CHARS: int = 2000


# ============================================================================
# 摘要生成函数
# ----------------------------------------------------------------------------
# 调用关系:
#     task_manager 检测到 token 超阈值 / 用户点一键压缩 ->
#         run_session_summary(...) -> 返回新摘要文本 ->
#         storage.update_session_summary(...) 写回 mongo
#
# 异步对象绑定:
#     ChatOpenAI 在 调用所在事件循环 即时创建  谁创建谁使用
#     不缓存到模块级避免跨 loop 复用导致 'Event loop is closed'
#
# 失败处理:
#     LLM 调用 / 超时 / 网络异常 一律向上抛
#     上层 task_manager 决定降级策略  常见做法是回到旧摘要不更新
# ============================================================================
import asyncio  # noqa: E402
from typing import Iterable  # noqa: E402

import structlog  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from ..core.models import AgentRecord  # noqa: E402

_logger = structlog.get_logger(__name__)


def render_history_for_summary(history: Iterable[dict]) -> str:
    """把 history dict 列表渲染成"用户: xxx\\n助手: xxx" 纯文本

    与 _build_history 的格式对齐  接受 [{"role":"user|assistant","content":"..."}]
    role 不识别的条目跳过 不做异常处理避免污染摘要正文
    content 为空字符串的也跳过 没必要给 LLM 看空轮
    """
    lines: list[str] = []
    for h in history:
        role = h.get("role")
        content = h.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            lines.append(f"用户: {content}")
        elif role == "assistant":
            lines.append(f"助手: {content}")
    return "\n\n".join(lines)


async def run_session_summary(
    *,
    history: list[dict],
    old_summary: str,
    agent_record: AgentRecord,
    timeout_s: float = 60.0,
) -> str:
    """跑会话摘要  返回新摘要正文(供 storage.update_session_summary 写回)

    参数:
        history: 待压缩的对话历史  形如 [{"role":"user|assistant","content":"..."}]
            调用方负责按 summary_until_round 之后的轮次截取  本函数不做范围裁剪
        old_summary: 上一版摘要  没有就传空字符串  内部转成"(无)"占位
        agent_record: 当前 reply agent 配置  复用其 base_url / api_key / model
        timeout_s: 单次摘要 LLM 调用超时  默认 60s

    返回:
        新摘要正文  可能因 LLM 输出超规导致超 SESSION_SUMMARY_MAX_CHARS
        上层不强制截断  避免破坏章节结构  仅作软上限提示

    抛出:
        asyncio.TimeoutError  超时
        其它 LLM / 网络异常 由 langchain_openai 抛出原样向上传递
    """
    if not history:
        # 没有对话可摘要 直接返回旧值不动  避免传空 history 给 LLM 触发降级输出
        _logger.info("run_session_summary 无 history 跳过", agent=agent_record.name)
        return old_summary

    rendered = render_history_for_summary(history)
    if not rendered.strip():
        _logger.info("run_session_summary 渲染后为空 跳过", agent=agent_record.name)
        return old_summary

    human_text = SESSION_SUMMARY_HUMAN_TEMPLATE.format(
        old_summary=old_summary.strip() if old_summary else "(无)",
        conversation=rendered,
    )

    # ChatOpenAI 在调用所在 loop 创建  不复用 reply 的实例避免 streaming 配置干扰
    # 摘要不需要流式 一次性拿完整文本即可
    model = ChatOpenAI(
        model=agent_record.model,
        api_key=agent_record.api_key,
        base_url=agent_record.base_url,
        streaming=False,
        timeout=timeout_s,
        max_retries=1,
    )

    state = await asyncio.wait_for(
        model.ainvoke(
            [
                SystemMessage(content=SESSION_SUMMARY_SYSTEM_PROMPT),
                HumanMessage(content=human_text),
            ]
        ),
        timeout=timeout_s,
    )

    # ChatOpenAI.ainvoke 直接返回 AIMessage  content 字段即正文
    content = getattr(state, "content", "")
    if isinstance(content, list):
        # 多模态返回时 content 是 list[dict] 取 type==text 部分
        content = "".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    text = str(content).strip()

    if not text:
        _logger.warning(
            "run_session_summary LLM 返回空内容 沿用旧摘要",
            agent=agent_record.name,
            model=agent_record.model,
        )
        return old_summary

    _logger.info(
        "run_session_summary 完成",
        agent=agent_record.name,
        model=agent_record.model,
        old_len=len(old_summary),
        new_len=len(text),
    )
    return text

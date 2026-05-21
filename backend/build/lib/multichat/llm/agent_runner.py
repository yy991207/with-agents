"""agent runner 三段式入口骨架

提供 think reply judge 三个 async 函数 由 TaskManager 编排:
    - run_think: 触发单个 think 用 agent 输出 50 字以内发言理由
    - run_reply: 在 reply 阶段驱动被选中 agent 流式生成最终回复
    - run_judge: judge 模型用于辅助评估 think 结果或兜底排序

约束:
    - 全部以协程形式实现 配合 asyncio.gather 并发触发
    - 通过 SSEStream 推送中间事件 不允许在非创建 loop 上调用
    - 异常必须被捕获并落到 ThinkResult.error / 任务失败状态 不向上抛
"""

from __future__ import annotations

from typing import Any

from ..core.models import ThinkResult


async def run_think(
    agent_name: str,
    question: str,
    *,
    task_id: str,
    deep_agent: Any,
) -> ThinkResult:
    """执行 think 阶段 返回单个 agent 的发言理由"""

    raise NotImplementedError("M2 实施")


async def run_reply(
    agent_name: str,
    question: str,
    *,
    task_id: str,
    deep_agent: Any,
    sse: Any,
) -> str:
    """执行 reply 阶段 流式推送中间事件 返回最终汇总文本"""

    raise NotImplementedError("M2 实施")


async def run_judge(
    question: str,
    think_results: list[ThinkResult],
    *,
    task_id: str,
    judge_agent: Any,
) -> dict[str, Any]:
    """执行 judge 阶段 用于辅助评估或兜底"""

    raise NotImplementedError("M2 实施")

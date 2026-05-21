"""TaskManager 任务编排骨架

负责:
    - 创建/查询/取消任务
    - 协调 think 阶段的并发触发与结果收集
    - 接收用户决策 触发 reply 阶段
    - 通过 SSE 推送状态与流式内容

并发安全要求:
    - 内部状态需通过锁保护 防止 think 与 cancel 竞态
    - 不同 task_id 间互相隔离 通过 RunnableConfig 把 task_id 注入到 deepagents 工具调用上下文

当前为 M1 骨架 方法均抛 NotImplementedError
"""

from __future__ import annotations

from typing import Any

from .models import Round, Session, TaskState


class TaskManager:
    """任务管理器 单例形式由应用工厂注入"""

    def __init__(self) -> None:
        # 后续会持有 storage llm_runner sse_hub 等依赖
        pass

    async def create_task(self, session_id: str, question: str) -> str:
        """创建一个新任务 返回 task_id 触发 think 阶段并发执行"""
        raise NotImplementedError("M2 实施")

    async def submit_decision(self, task_id: str, chosen_agent: str) -> None:
        """用户选择某个 agent 后驱动 reply 阶段"""
        raise NotImplementedError("M2 实施")

    async def cancel_task(self, task_id: str) -> None:
        """取消任务 涉及取消 think/reply 阶段未完成的协程"""
        raise NotImplementedError("M2 实施")

    async def retry_think(self, task_id: str) -> None:
        """重新触发 think 阶段 用于个别 agent 失败时的恢复"""
        raise NotImplementedError("M2 实施")

    async def get_state(self, task_id: str) -> TaskState:
        """查询任务当前状态"""
        raise NotImplementedError("M2 实施")

    async def get_round(self, task_id: str) -> Round:
        """查询任务对应的 Round 数据"""
        raise NotImplementedError("M2 实施")

    async def list_sessions(self) -> list[Session]:
        """会话列表查询 转交 storage 层"""
        raise NotImplementedError("M2 实施")

    async def emit_event(self, task_id: str, event: dict[str, Any]) -> None:
        """对外推送 SSE 事件 由 LLM runner 与状态机内部调用"""
        raise NotImplementedError("M2 实施")

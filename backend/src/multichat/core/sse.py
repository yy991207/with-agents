"""SSE 流式推送辅助类骨架

设计要点:
    - 每个 task_id 维护一条独立的 asyncio.Queue
    - 生产者由 TaskManager / agent_runner 调用 push_event
    - 消费者在 routes/stream.py 中作为 SSE 响应迭代器
    - 关闭时需要 graceful 释放队列 防止悬挂连接

异步对象与事件循环绑定问题(参考全局规范):
    - Queue 必须在使用它的 loop 中创建
    - 严禁跨线程直接 push 跨线程时使用 run_coroutine_threadsafe 投递

当前为 M1 骨架 真实推送逻辑在 M2 阶段补齐
"""

from __future__ import annotations

from typing import Any, AsyncIterator


class SSEStream:
    """单个 SSE 通道封装"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    async def push_event(self, event: dict[str, Any]) -> None:
        """生产者接口 由内部调用"""
        raise NotImplementedError("M2 实施")

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        """消费者迭代器 供 sse-starlette 使用"""
        raise NotImplementedError("M2 实施")
        # 占位 yield 让类型检查识别为异步生成器
        if False:
            yield {}

    async def close(self) -> None:
        """优雅关闭通道 释放队列资源"""
        raise NotImplementedError("M2 实施")

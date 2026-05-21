"""任务事件总线

每个 task 一个独立 hub
订阅者拿 asyncio.Queue 接收事件 publish 不阻塞 队列默认无界
hub 维护历史 history 列表给后到的 SSE 订阅者用 snapshot 重放

异步对象与事件循环绑定问题(参考全局规范):
    - hub 内部所有 asyncio.Queue 与 Lock 都必须在使用它的 loop 中创建
    - 创建 hub 与消费 hub 必须在同一个事件循环 不可跨线程跨 loop
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskEvent:
    """SSE 事件 payload 结构

    type 与 spec §6.2 严格对齐 取值参考:
        snapshot
        task.state
        think.start | think.done | think.failed | think.cancelled
        judge.start | judge.done
        reply.start | reply.chunk | reply.tool_call | reply.tool_result
        reply.done | reply.error
        task.unrecoverable
    data 是结构化 payload 直接 json.dumps 写到 SSE data 行
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转 dict 用于 snapshot 帧打包以及 SSE 协议化"""
        return {"type": self.type, "data": self.data}


class TaskEventHub:
    """单 task 的事件广播中枢

    设计要点:
        - history 列表只增不删 给后到的订阅者用作 snapshot 重放
        - subscribers 是活跃订阅者的 queue 列表 publish 时同步 put_nowait
        - close 后置位 _closed 新订阅者 subscribe 立刻拿到 None 提前关流
        - publish/subscribe/unsubscribe/close 通过 _lock 串行 防止并发改 list
        - asyncio.Queue 默认无界 publish 不阻塞 即便订阅者消费慢也不会卡 publisher
    """

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._subscribers: list[asyncio.Queue[TaskEvent | None]] = []
        self._history: list[TaskEvent] = []
        self._closed: bool = False
        self._lock = asyncio.Lock()

    async def publish(self, event: TaskEvent) -> None:
        """广播事件 不阻塞调用方 即便没有订阅者也会被记入 history 给后续 snapshot 用"""
        async with self._lock:
            self._history.append(event)
            for q in list(self._subscribers):
                # Queue 无界 put_nowait 安全 不会因满而抛 QueueFull
                q.put_nowait(event)

    async def subscribe(
        self,
    ) -> tuple[list[TaskEvent], asyncio.Queue[TaskEvent | None]]:
        """订阅事件流 返回 (历史快照, 新事件 queue)

        订阅者先消费 snapshot 再监听 queue
        若 hub 已 close 直接返回历史 + 立刻置 None 关流的 queue
        """
        q: asyncio.Queue[TaskEvent | None] = asyncio.Queue()
        async with self._lock:
            history = list(self._history)
            if self._closed:
                # 已关流 让订阅者 snapshot 后立刻收到 None 退出循环
                q.put_nowait(None)
            else:
                self._subscribers.append(q)
        return history, q

    async def unsubscribe(self, q: asyncio.Queue[TaskEvent | None]) -> None:
        """注销订阅者 客户端断开时由 SSE handler 调用"""
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def close(self) -> None:
        """关闭 hub 通知所有订阅者退出 loop

        关流后仍允许 subscribe 但新订阅者只拿 history 然后立刻收到 None
        """
        async with self._lock:
            self._closed = True
            for q in list(self._subscribers):
                q.put_nowait(None)
            self._subscribers.clear()

"""简易令牌桶 / 滑动窗口计数器 用于 judge 等场景的软限流

设计目标:
    - 进程内不持久化 仅做软限流 不做精确分布式控制
    - 单 RateLimiter 实例对应一个限流维度  比如全局 judge 调用
    - 超额时 raise RateLimitExceeded 路由层捕获后返 429

使用方式:
    limiter = RateLimiter("judge", RateLimit(capacity=10, window_s=60))
    await limiter.check()  # 命中阈值则抛 RateLimitExceeded
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class RateLimit:
    """限流参数 capacity 表示窗口内允许次数 window_s 是窗口大小(秒)"""

    capacity: int
    window_s: float


class RateLimitExceeded(Exception):
    """超出限流时抛出 携带名字与建议等待秒数 由路由层映射 429"""

    def __init__(self, name: str, wait_s: float) -> None:
        self.name = name
        self.wait_s = wait_s
        super().__init__(
            f"{name} 限流 请 {max(1, int(round(wait_s)))} 秒后重试"
        )


class RateLimiter:
    """滑动窗口计数器  每次 check 把过期事件出队 超阈值 raise

    线程/loop 安全说明:
        - 内部用 asyncio.Lock 保护事件队列  必须在创建它的 loop 中使用
        - 不跨进程  进程级软限流  生产化建议改 redis 之类
    """

    def __init__(self, name: str, limit: RateLimit) -> None:
        self._name = name
        self._limit = limit
        # 单调时钟避免系统时间回拨干扰
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def check(self) -> None:
        """登记一次调用 若超阈值抛 RateLimitExceeded 否则记录后返回"""
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._limit.window_s
            # 把超过窗口的旧事件出队
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            if len(self._events) >= self._limit.capacity:
                # 队首是最早的有效事件 等它出窗口才算腾出空位
                wait_s = self._limit.window_s - (now - self._events[0])
                raise RateLimitExceeded(self._name, wait_s if wait_s > 0 else 0.0)
            self._events.append(now)

    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._limit.capacity

    @property
    def window_s(self) -> float:
        return self._limit.window_s

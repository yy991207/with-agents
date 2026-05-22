"""RateLimiter / RateLimit 单元测试

只关注两件事:
    - 在容量内允许通过
    - 超过容量抛 RateLimitExceeded
窗口滑动靠 monotonic time 单调时钟 测试时直接调小 window 即可观察
"""

from __future__ import annotations

import asyncio

import pytest

from multichat.core.rate_limit import (
    RateLimit,
    RateLimitExceeded,
    RateLimiter,
)


@pytest.mark.asyncio
async def test_rate_limit_allows_under_capacity() -> None:
    """capacity=10 窗口内 10 次都应放行"""
    limiter = RateLimiter("judge", RateLimit(capacity=10, window_s=60.0))
    for _ in range(10):
        await limiter.check()  # 不应抛


@pytest.mark.asyncio
async def test_rate_limit_blocks_over_capacity() -> None:
    """capacity=10 第 11 次抛 RateLimitExceeded 携带等待秒数"""
    limiter = RateLimiter("judge", RateLimit(capacity=10, window_s=60.0))
    for _ in range(10):
        await limiter.check()
    with pytest.raises(RateLimitExceeded) as ei:
        await limiter.check()
    assert ei.value.name == "judge"
    # 等待秒数应在窗口范围内
    assert 0 <= ei.value.wait_s <= 60.0


@pytest.mark.asyncio
async def test_rate_limit_window_slides() -> None:
    """窗口过期后 旧事件出队 新调用应放行"""
    limiter = RateLimiter("judge", RateLimit(capacity=2, window_s=0.1))
    await limiter.check()
    await limiter.check()
    with pytest.raises(RateLimitExceeded):
        await limiter.check()
    # 等到窗口外 旧事件应被清掉
    await asyncio.sleep(0.15)
    # 此时新调用应能放行
    await limiter.check()


@pytest.mark.asyncio
async def test_rate_limit_concurrent_safe() -> None:
    """并发 check 用 lock 保护 不会出现读写错乱"""
    limiter = RateLimiter("judge", RateLimit(capacity=5, window_s=60.0))
    results = await asyncio.gather(
        *[limiter.check() for _ in range(5)],
        return_exceptions=True,
    )
    # 5 次都应成功
    assert all(r is None for r in results)
    with pytest.raises(RateLimitExceeded):
        await limiter.check()

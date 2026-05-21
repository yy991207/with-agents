"""端到端流程占位测试 当前 M1 骨架仅放置 skip 占位 真实链路在 M2 阶段补齐"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="M2 阶段补齐:真实 e2e 需要 mongomock-motor + httpx AsyncClient 串起 ask/decide/sse/history")
def test_full_think_then_choose_flow() -> None:
    """模拟用户提问 → 4 路 think 完成 → 选择 → reply 流式 → 历史落库"""
    raise AssertionError("占位 不应被实际执行")

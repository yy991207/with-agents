"""核心 Pydantic 数据模型骨架

包含 think-then-choose 流程中的关键实体:
    - TaskState: 任务状态机枚举
    - ThinkResult: 单个 LLM 在 think 阶段产出的发言理由
    - Round: 一轮交互的完整上下文 含 4 个 think 结果与最终被选中 LLM 的回复
    - Session: 一次会话 包含多轮 Round 与会话级元数据

字段为 M1 占位 后续按业务真实落地时迭代
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskState(str, Enum):
    """任务状态机 覆盖 think → 等待用户选择 → reply → 完成/取消/失败 全链路"""

    CREATED = "created"
    THINKING = "thinking"
    WAITING_DECISION = "waiting_decision"
    REPLYING = "replying"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ThinkResult(BaseModel):
    """单个 LLM 在 think 阶段产出的发言理由"""

    agent_name: str
    reason: str = ""
    latency_ms: int = 0
    error: str | None = None


class Round(BaseModel):
    """一轮交互上下文 一次提问触发的完整 think + reply 周期"""

    round_id: str
    question: str
    think_results: list[ThinkResult] = Field(default_factory=list)
    chosen_agent: str | None = None
    reply_content: str = ""
    state: TaskState = TaskState.CREATED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Session(BaseModel):
    """一次会话 含若干轮 Round"""

    session_id: str
    title: str = ""
    rounds: list[Round] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

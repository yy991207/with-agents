"""核心 Pydantic 数据模型

包含 think-then-choose 流程中的关键实体:
    - TaskState: 任务状态机枚举
    - ThinkResult: 单个 LLM 在 think 阶段产出的发言理由
    - Round: 一轮交互的完整上下文 含 4 个 think 结果与最终被选中 LLM 的回复
    - Session: 一次会话 包含多轮 Round 与会话级元数据
    - SessionMeta: 会话列表展示用的轻量元信息
    - AgentRecord: agents collection 中的一条记录 仅存对话 agent
        裁判选谁存放在独立的 settings collection judge_pointer 文档里 与本模型无关
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """统一带时区的当前时间 避免 naive datetime 在 mongo 落库时跑偏"""
    return datetime.now(timezone.utc)


class TaskState(str, Enum):
    """任务状态机 覆盖 think → 等待用户选择 → reply → 完成/取消/失败 全链路

    历史值与 spec 值并存:
        - 历史值 CREATED/WAITING_DECISION/FAILED 用于早期 storage 兼容 不再产生新记录
        - spec 值 PENDING/THINKING/THINK_DONE/DECIDED/REPLYING/DONE/CANCELLED 由 task_manager 使用
    """

    # 历史值 仍被 storage.create_round 默认写入 同时也是兼容老快照所必需
    CREATED = "created"
    WAITING_DECISION = "waiting_decision"
    FAILED = "failed"

    # 与 think-then-choose spec §4 状态机对齐
    PENDING = "pending"
    THINKING = "thinking"
    THINK_DONE = "think_done"
    DECIDED = "decided"
    REPLYING = "replying"
    DONE = "done"
    CANCELLED = "cancelled"


class ThinkResult(BaseModel):
    """单个 LLM 在 think 阶段产出的发言理由"""

    agent_name: str
    reason: str = ""
    latency_ms: int = 0
    error: str | None = None


class Round(BaseModel):
    """一轮交互上下文 一次提问触发的完整 think + reply 周期

    task_id 是 round 全局唯一 id 即对外暴露给前端的 SSE 路径参数

    字段分两套:
        - 历史字段: think_results / chosen_agent / reply_content 由 storage.create_round
          默认写入 与 M0/M1 兼容
        - spec 字段: thinks / decision / reply / think_history 由 task_manager 在运行时
          通过 update_round_field 增量写入 历史回放与 SSE snapshot 都基于这套
    """

    task_id: str
    session_id: str
    round_index: int = 0
    question: str
    user_mention: str | None = None
    # 历史字段 早期持久化结构 兼容已有数据
    think_results: list[ThinkResult] = Field(default_factory=list)
    chosen_agent: str | None = None
    reply_content: str = ""
    state: TaskState = TaskState.CREATED
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # spec 字段 task_manager 运行时使用 默认空 兼容旧数据
    # thinks 形如 {"GLM": {"state":"done","content":"..."}, ...}
    thinks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # decision 形如 {"choice":"GLM","reason":"user_pick","decided_at":"..."}
    decision: dict[str, Any] | None = None
    # reply 形如 {"agent":"GLM","state":"streaming|done|failed","content":"..."}
    reply: dict[str, Any] | None = None
    # think_history 用于 regenerate 之后保留上一轮 think 结果
    think_history: list[dict[str, Any]] = Field(default_factory=list)


class Session(BaseModel):
    """一次会话 含若干轮 Round"""

    session_id: str
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SessionMeta(BaseModel):
    """会话列表展示用 轻量元信息 不带 rounds 详细内容"""

    session_id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime


class AgentRecord(BaseModel):
    """agents 集合中的一条记录 仅存对话 agent 配置

    name 取值如 DeepSeek/GLM/Kimi/Qwen 全局唯一
    kind 当前固定为 agent 留出枚举位为日后扩展 agent 子类型预留
    version 每次 upsert 自增 1 用于前端比对是否本地缓存过期
    """

    name: str
    model: str
    prompt: str
    kind: Literal["agent"] = "agent"
    version: int = 1
    updated_at: datetime = Field(default_factory=_utcnow)

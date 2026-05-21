"""MongoStorage 抽象接口 用 Protocol 描述

后端用 motor 实现 测试用 mongomock-motor 实现 二者皆满足该协议
所有方法均为 async 由调用方自行决定是否阻塞等待

业务层只接触 string 形式的 session_id / task_id ObjectId 不外泄
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..core.models import AgentRecord, Round, Session, SessionMeta, TaskState


@runtime_checkable
class MongoStorage(Protocol):
    """会话 轮次 agents 三类资源的持久化协议"""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def ensure_indexes(self) -> None: ...

    # ------------------------------------------------------------------ Sessions
    async def create_session(self, title: str | None = None) -> str: ...

    async def list_sessions(self, limit: int = 50) -> list[SessionMeta]: ...

    async def get_session(self, session_id: str) -> Session | None: ...

    async def update_session_meta(
        self, session_id: str, *, title: str | None = None
    ) -> None: ...

    # -------------------------------------------------------------------- Rounds
    async def create_round(
        self,
        session_id: str,
        user_message: str,
        user_mention: str | None,
    ) -> str: ...

    async def get_round(self, task_id: str) -> Round | None: ...

    async def list_rounds(self, session_id: str) -> list[Round]: ...

    async def update_round_state(self, task_id: str, state: TaskState) -> None: ...

    async def update_round_field(self, task_id: str, path: str, value: Any) -> None: ...

    async def append_reply_chunk(self, task_id: str, chunk: str) -> None: ...

    async def cancel_orphan_rounds(self, reason: str = "server_restart") -> int: ...

    # -------------------------------------------------------------------- Agents
    async def list_agents(self) -> list[AgentRecord]: ...

    async def get_agent(self, name: str) -> AgentRecord | None: ...

    async def upsert_agent(
        self,
        name: str,
        model: str,
        prompt: str,
    ) -> AgentRecord: ...

    async def get_judge_target(self) -> str: ...

    async def set_judge_target(self, agent_name: str) -> None: ...

    async def seed_from_yaml(self, settings: Any) -> int: ...

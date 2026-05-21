"""MongoStorage 抽象接口 用 Protocol 描述

后端用 motor 实现 测试用 mongomock-motor 实现 二者皆满足该协议
所有方法均为 async 由调用方自行决定是否阻塞等待
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..core.models import Round, Session


@runtime_checkable
class MongoStorage(Protocol):
    """会话与轮次持久化协议"""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def save_session(self, session: Session) -> None: ...

    async def get_session(self, session_id: str) -> Session | None: ...

    async def list_sessions(self, limit: int = 50) -> list[Session]: ...

    async def upsert_round(self, session_id: str, round_data: Round) -> None: ...

    async def get_round(self, session_id: str, round_id: str) -> Round | None: ...

    async def append_event(self, task_id: str, event: dict[str, Any]) -> None: ...

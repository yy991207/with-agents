"""基于 motor 的 MongoStorage 实现骨架

实现要点:
    - AsyncIOMotorClient 在 connect 阶段创建 close 阶段释放
    - 客户端实例与事件循环强绑定 必须在使用它的 loop 中创建
    - 所有写操作具备幂等性 通过 _id 或业务唯一键 upsert
    - 异常向上抛 由 TaskManager 决定降级策略

当前为 M1 骨架 真实方法体在 M2 阶段补齐
"""

from __future__ import annotations

from typing import Any

from ..core.models import Round, Session


class MotorMongoStorage:
    """基于 motor 的 MongoStorage 实现"""

    def __init__(self, uri: str, database: str) -> None:
        self.uri = uri
        self.database = database
        # 实际客户端在 connect 中初始化 避免在错误的 loop 中创建
        self._client: Any = None
        self._db: Any = None

    async def connect(self) -> None:
        raise NotImplementedError("M2 实施")

    async def close(self) -> None:
        raise NotImplementedError("M2 实施")

    async def save_session(self, session: Session) -> None:
        raise NotImplementedError("M2 实施")

    async def get_session(self, session_id: str) -> Session | None:
        raise NotImplementedError("M2 实施")

    async def list_sessions(self, limit: int = 50) -> list[Session]:
        raise NotImplementedError("M2 实施")

    async def upsert_round(self, session_id: str, round_data: Round) -> None:
        raise NotImplementedError("M2 实施")

    async def get_round(self, session_id: str, round_id: str) -> Round | None:
        raise NotImplementedError("M2 实施")

    async def append_event(self, task_id: str, event: dict[str, Any]) -> None:
        raise NotImplementedError("M2 实施")

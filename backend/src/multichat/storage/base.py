"""MongoStorage 抽象接口 用 Protocol 描述

后端用 motor 实现 测试用 mongomock-motor 实现 二者皆满足该协议
所有方法均为 async 由调用方自行决定是否阻塞等待

业务层只接触 string 形式的 session_id / task_id ObjectId 不外泄
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..core.models import AgentRecord, ProviderProfile, Round, Session, SessionMeta, TaskState


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

    async def delete_session(self, session_id: str) -> int:
        """删除 session 与其下所有 rounds  返回删除的 round 数

        约束:
            - session 不存在抛 KeyError
            - 若 session 下还有进行中的 round (state ∈ pending/thinking/think_done/decided/replying)
              抛 ValueError 防止误删活动会话
        """
        ...

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
        kind: str = "agent",
        profile_name: str | None = None,
    ) -> AgentRecord: ...

    async def list_agent_history(
        self, name: str, limit: int = 20
    ) -> list[dict]:
        """列出某个 agent 的历史版本 按 version 降序

        返回字典列表 字段含 name model prompt version archived_at archived_reason
        路由层负责再序列化为对外 schema  storage 这层不强制 AgentRecord 类型
        """
        ...

    async def get_agent_history(
        self, name: str, version: int
    ) -> dict | None:
        """取指定 agent 的指定历史版本字典 不存在返回 None"""
        ...

    async def get_judge_target(self) -> str: ...

    async def set_judge_target(self, agent_name: str) -> None: ...

    # ----------------------------------------------------------- ProviderProfiles
    async def list_profiles(self) -> list[ProviderProfile]: ...

    async def get_profile(self, name: str) -> ProviderProfile | None: ...

    async def create_profile(self, profile: ProviderProfile) -> ProviderProfile:
        """新建 profile 同名已存在抛 ValueError"""
        ...

    async def update_profile(
        self,
        name: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        models: list | None = None,
        provider_type: str | None = None,
    ) -> ProviderProfile:
        """局部更新 profile  仅更新非 None 字段  不存在抛 KeyError  version+1"""
        ...

    async def delete_profile(self, name: str) -> None:
        """删除 profile  仍被任意 agent 引用时抛 ValueError 路由层映射 409"""
        ...

    async def seed_from_yaml(self, settings: Any) -> int: ...

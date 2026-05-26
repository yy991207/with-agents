"""MongoStorage 抽象接口 用 Protocol 描述

后端用 motor 实现 测试用 mongomock-motor 实现 二者皆满足该协议
所有方法均为 async 由调用方自行决定是否阻塞等待

业务层只接触 string 形式的 session_id / task_id ObjectId 不外泄
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from ..core.models import AgentRecord, McpServerConfig, ModelCatalogEntry, Round, Session, SessionMeta, TaskState


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
            - 若 session 下还有进行中的 round (state ∈ pending/replying)
              抛 ValueError 防止误删活动会话
        """
        ...

    # -------------------------------------------------------------------- Rounds
    async def create_round(
        self,
        session_id: str,
        user_message: str,
        user_mention: str | None,
        agents: list[str],
        input_mode: Literal["single", "multi"] = "single",
        thinking_enabled: bool = False,
    ) -> str:
        """创建一轮新提问

        agents 必须是非空列表 长度 1~4
            单 agent 时 input_mode='single'  reply 完成时自动写 selected_reply_agent
            多 agent 时 input_mode='multi'   需要用户调 /select_reply 才会有 selected_reply_agent
        replies 字段初始化为 {agent: {"state":"pending","content":"","segments":[]}, ...}

        校验在路由层做  这里只负责落库
        """
        ...

    async def get_round(self, task_id: str) -> Round | None: ...

    async def list_rounds(self, session_id: str) -> list[Round]: ...

    async def update_round_state(self, task_id: str, state: TaskState) -> None: ...

    async def update_round_field(self, task_id: str, path: str, value: Any) -> None: ...

    async def delete_rounds_after(self, session_id: str, round_index: int) -> int: ...

    async def append_reply_chunk_for_agent(
        self, task_id: str, agent_name: str, chunk: str
    ) -> None:
        """流式追加单个 agent 的 reply.content 节流写库

        与历史 append_reply_chunk 区别:
            写到 replies.{agent_name}.content 而不是顶层 reply.content
            多 agent 并发同 task 时各 agent 互不干扰
        """
        ...

    async def update_reply_segments_for_agent(
        self, task_id: str, agent_name: str, segments: list[dict[str, Any]]
    ) -> None:
        """整组覆盖写 replies.{agent_name}.segments  按时间顺序的段时间线"""
        ...

    async def update_reply_for_agent(
        self,
        task_id: str,
        agent_name: str,
        reply: dict[str, Any],
    ) -> None:
        """覆盖写 replies.{agent_name} 整段 reply 状态  含 state/content/segments/started_at/finished_at/error 等"""
        ...

    async def select_reply(self, task_id: str, agent_name: str) -> None:
        """用户从多 agent 候选中选定一个作为正式回答

        校验:
            - round 不存在抛 KeyError
            - agent_name 不在 round.agents 抛 ValueError
            - replies[agent_name].state 不是 done 抛 ValueError 不允许选中失败/未完成的回答
            - 已选过则覆盖更新(允许改主意)  不抛错
        """
        ...

    async def clear_session_summary(self, session_id: str) -> None: ...

    async def cancel_orphan_rounds(self, reason: str = "server_restart") -> int:
        """启动时清理孤儿 round  把所有进行中状态的 round 与其 replies 置为 cancelled"""
        ...

    # -------------------------------------------------------------------- Agents
    async def list_agents(self) -> list[AgentRecord]: ...

    async def get_agent(self, name: str) -> AgentRecord | None: ...

    async def create_agent(
        self,
        name: str | None,
        display_name: str,
        base_url: str,
        api_key: str,
        model: str,
        prompt: str,
        available_models: list[ModelCatalogEntry] | None = None,
        provider_type: str = "openai_compatible",
    ) -> AgentRecord:
        """新建 agent  name 不传则自动生成 agent_<8位hex>  name 重复抛 ValueError"""
        ...

    async def delete_agent(self, name: str) -> None:
        """删除 agent  若该 name 是当前 judge_target 抛 ValueError 路由层映射 409  不存在抛 KeyError"""
        ...

    async def upsert_agent(
        self,
        name: str,
        *,
        display_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        available_models: list | None = None,
        prompt: str | None = None,
        provider_type: str | None = None,
    ) -> AgentRecord:
        """部分更新 agent  None 表示保留旧值  version 自增  不存在抛 KeyError"""
        ...

    async def list_agent_history(
        self, name: str, limit: int = 20
    ) -> list[dict]:
        """列出某个 agent 的历史版本 按 version 降序

        返回字典列表 字段含 name/display_name/base_url/api_key/model/available_models/
        prompt/provider_type/version/archived_at/archived_reason
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

    async def seed_from_yaml(self, settings: Any) -> int: ...

    # ------------------------------------------------------------- MCP Servers
    async def list_mcp_servers(self) -> list[McpServerConfig]:
        """列出所有 MCP 服务器配置 按 name 升序"""
        ...

    async def upsert_mcp_server(self, server: McpServerConfig) -> McpServerConfig:
        """创建或全量覆盖单个 MCP 服务器配置 按 name 唯一"""
        ...

    async def delete_mcp_server(self, name: str) -> None:
        """删除 MCP 服务器 不存在抛 KeyError"""
        ...

"""deepagents 实例构建器 提供 think + reply 双实例预创建与热替换能力

预创建策略:
    每个 agent 同时构造两个 deep_agent 实例
    - {agent_name}-think: system_prompt 强约束 50 字以内 不调用任何工具
    - {agent_name}-reply: 完整能力 鼓励规划+工具调用 流式回复
    任意数量 agent 都允许 启动时 initialize 一次 之后改 agent 配置走 reload
    新增 agent 也走 reload 追加 删除 agent 走 unregister 移除

MCP 工具注入(2026-05-23):
    reply 模式构建时会从数据库 mcp_config 文档读取 MCP 服务器配置
    通过 langchain_mcp_adapters 加载工具并注入到 agent 的工具列表中
    每个 MCP server 独立容错 加载失败只 warn 不阻塞 agent 初始化

并发安全:
    - 读端不加锁 dict.get 是 GIL 内原子操作 替换字典 value 也是原子的
    - 写端用 asyncio.Lock 串行化 避免两次 reload 中间态被并发读到 1 新 1 旧
    - 实例 build 在锁外 仅最终 swap 写入 dict 在锁内 减少锁持有时间
    - registry 与底层 LLM 客户端均为异步对象 必须在使用它的事件循环中创建
      谁创建谁使用 不可在不同 loop 间复用 详见全局规范
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import structlog
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langchain_openai import ChatOpenAI

from .chat_models import ReasoningChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from ..config import Settings
from ..core.models import AgentRecord

_logger = structlog.get_logger(__name__)

# 实例 kind 枚举 think 用于 50 字理由 reply 用于完整作答
DeepAgentKind = Literal["think", "reply"]


# think 模式系统提示后缀 强约束输出长度且禁止工具调用
THINK_SYSTEM_SUFFIX = """

[当前任务] 用户提了一个问题 你需要在 50 个汉字以内说一句话
解释你为什么适合回答这个问题
硬约束
 1 输出不超过 50 个汉字
 2 严禁调用任何工具
 3 严禁分点列项 直接一句话
 4 不要重复或转述用户的问题
"""


# reply 模式系统提示后缀 鼓励规划+工具调用
REPLY_SYSTEM_SUFFIX = """

[当前任务] 用户给你一个具体可执行的任务 根据任务特点自主选择工具完成
完成后用一句话告诉用户做了什么

输出格式: 使用 HTML 标签格式化回复 不要输出 Markdown
- 段落用 <p> 标题用 <h2>/<h3> 列表用 <ul><li>
- 加粗用 <strong> 代码用 <pre><code> 链接用 <a href="...">
"""

# MCP 工具说明模板 在 _build_one 加载完 MCP 工具后动态拼接
# {server_list}: 已启用的 MCP 服务器名称列表 如 "playwright, sequential-thinking"
# {tool_count}: 从这些服务器加载的工具总数
MCP_SYSTEM_PROMPT = """

## 关于 MCP (Model Context Protocol) 扩展工具

你的部分工具来自 MCP 服务器——这些是外部进程通过标准协议提供的能力
运行时由管理员在后台配置 你无需关心它们的启动和连接细节

当前已挂载的 MCP 服务器: {server_list}
从这些服务器加载的工具共 {tool_count} 个

使用要点:
- MCP 工具与内置工具地位相同 遇到任务时自然选用 无需额外声明"这是 MCP 工具"
- MCP 服务器的工具列表会随管理员配置变动 以当前对话中实际可用的工具为准
- 回答用户关于"你有哪些能力"的问题时 全部可用工具都属于你的能力
  无需区分"内置"和"MCP" 直接列出即可
- 如果用户明确问"你接了哪些 MCP 服务" 就如实告诉用户上述服务器列表
"""

def _build_document_backend_and_permissions(
    settings: Settings,
) -> tuple[CompositeBackend, list[Any]]:
    """为 reply 模式构造默认整机可见的文件系统 backend

    设计取舍:
        - 默认 backend 指向整机根目录 /  让模型默认可见宿主机所有目录
        - 保留 CompositeBackend 形态 便于后续需要时再扩展额外挂载
        - 不再使用白名单权限 rules  模型直接使用虚拟绝对路径访问整机文件
    """
    default_backend = FilesystemBackend(root_dir=Path("/"), virtual_mode=True)
    backend = CompositeBackend(default=default_backend, routes={})
    return backend, []


async def _build_one(
    agent_record: AgentRecord,
    kind: DeepAgentKind,
    settings: Settings,
    *,
    storage: Any | None = None,
    thinking_enabled: bool = False,
    owner_user_id: str | None = None,
) -> CompiledStateGraph:
    """根据 agent 配置构造一个 deep_agent 实例

    owner_user_id 用于按用户加载 MCP 工具和 Skills 内容
    为 None 时跳过 MCP/Skills 加载（系统启动初始化场景）
    """
    suffix = THINK_SYSTEM_SUFFIX if kind == "think" else REPLY_SYSTEM_SUFFIX
    system_prompt = agent_record.prompt + suffix
    # 思考模式仅 reply 阶段生效  think 阶段是 50 字理由 不需要深度思考
    # langchain_openai 把 extra_body 当 ChatOpenAI 顶层参数  传进 model_kwargs 会被剥离并 UserWarning
    # 所以这里直接走顶层  None 表示未开启 _default_params 不会包含
    extra_body: dict[str, Any] | None = None
    if kind == "reply" and thinking_enabled:
        extra_body = {"thinking": {"type": "enabled"}}
    # ChatOpenAI 仅在创建时拿凭据 真正网络调用发生在 ainvoke/astream 时
    # 因此这里多次重建实例不会发起网络请求 测试可以放心调用
    chat_kwargs: dict[str, Any] = dict(
        model=agent_record.model,
        api_key=agent_record.api_key,
        base_url=agent_record.base_url,
        streaming=(kind == "reply"),
        timeout=settings.runtime.http_timeout_seconds,
        max_retries=1,
    )
    if extra_body is not None:
        chat_kwargs["extra_body"] = extra_body
    # 用 ReasoningChatOpenAI 替代官方 ChatOpenAI  让 reasoning_content 字段不被丢弃
    # 否则阿里百炼 / DeepSeek 等吐 reasoning_content 的模型  langchain 转 chunk 时会扔掉
    # 前端永远看不到深度思考内容
    model = ReasoningChatOpenAI(**chat_kwargs)

    # 挂工具策略
    #   reply 模式 注入共享 tool(current_time/http_get) + MCP 工具
    #   think 模式 显式空 prompt 已禁止调工具 也不挂 tool 防 LLM 误判
    mcp_servers: list[str] = []
    mcp_tool_count = 0
    skill_names: list[str] = []
    if kind == "reply":
        from .tools import get_shared_tools, load_mcp_tools_from_db, load_skills_from_db

        tools = get_shared_tools()
        # MCP 工具加载 按 owner_user_id 过滤当前用户的配置
        if storage is not None and owner_user_id is not None:
            mcp_tools, mcp_servers = await load_mcp_tools_from_db(storage, owner_user_id=owner_user_id)
            mcp_tool_count = len(mcp_tools)
            if mcp_tools:
                tools = [*tools, *mcp_tools]

            # Skills 内容加载 追加到 system_prompt 不产生 tool
            skills_text, skill_names = await load_skills_from_db(storage, owner_user_id=owner_user_id)
            if skills_text:
                system_prompt += skills_text
    else:
        tools = []

    backend = None
    permissions = None
    if kind == "reply":
        backend, permissions = _build_document_backend_and_permissions(settings)

    # MCP 工具已加载时 在 system_prompt 尾部追加一段 MCP 概念说明
    # 让 LLM 明确知道这些工具来自外部 MCP 服务器 回答"你有哪些能力"时无需区分来源
    if mcp_servers:
        system_prompt += MCP_SYSTEM_PROMPT.format(
            server_list=", ".join(mcp_servers),
            tool_count=str(mcp_tool_count),
        )

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        permissions=permissions,
        name=f"{agent_record.name}-{kind}",
    )


class DeepAgentRegistry:
    """每个 agent 各自 think + reply 两实例的注册表

    支持热替换 改了某个 agent 配置后调 reload(record) 原子 swap 进字典
    新增 agent 也走 reload 追加 删除 agent 走 unregister 移除
    读端不加锁 写端用 asyncio.Lock 串行化

    内部持有 settings 与 storage 引用:
        - settings 传给 _build_one 用于 ChatOpenAI 超时等运行时参数
        - storage 传给 _build_one 用于 reply 模式从 DB 加载 MCP 工具
    """

    def __init__(self, settings: Settings, storage: Any | None = None) -> None:
        self._settings = settings
        self._storage = storage
        # key 格式: 无用户时 {agent_name}-{kind}  有用户时 {user_id}:{agent_name}-{kind}
        self._instances: dict[str, CompiledStateGraph] = {}
        self._versions: dict[str, int] = {}
        self._global_version: int = 0
        self._lock = asyncio.Lock()

    async def initialize(self, records: list[AgentRecord]) -> None:
        """启动时一次性 build 所有实例 不加载 MCP/Skills (无用户上下文)

        records 为空 names() 返回空列表 后续可通过 reload 动态新增
        """
        new_inst: dict[str, CompiledStateGraph] = {}
        for r in records:
            new_inst[f"{r.name}-think"] = await _build_one(
                r, "think", self._settings, storage=self._storage, owner_user_id=None
            )
            new_inst[f"{r.name}-reply"] = await _build_one(
                r, "reply", self._settings, storage=self._storage, owner_user_id=None
            )
        async with self._lock:
            self._instances = new_inst
            self._global_version += 1
        _logger.info("deep_agents 初始化完成", count=len(new_inst))

    async def reload(self, record: AgentRecord) -> None:
        """热替换 / 新增某个 agent 的 2 个实例 (系统级 无用户上下文)"""
        new_think = await _build_one(
            record, "think", self._settings, storage=self._storage, owner_user_id=None
        )
        new_reply = await _build_one(
            record, "reply", self._settings, storage=self._storage, owner_user_id=None
        )
        async with self._lock:
            self._instances[f"{record.name}-think"] = new_think
            self._instances[f"{record.name}-reply"] = new_reply
            self._global_version += 1
        _logger.info("deep_agents 热替换", name=record.name, version=record.version)

    def unregister(self, name: str) -> None:
        """删除 agent 时同步移除其 think+reply 实例及所有按用户缓存"""
        # 移除系统级实例
        self._instances.pop(f"{name}-think", None)
        self._instances.pop(f"{name}-reply", None)
        # 移除所有按用户缓存 (key 格式 {user_id}:{name}-{kind})
        to_remove = [k for k in self._instances if k.endswith(f":{name}-think") or k.endswith(f":{name}-reply")]
        for k in to_remove:
            self._instances.pop(k, None)
            self._versions.pop(k, None)
        _logger.info("deep_agents 注销", name=name)

    def get(self, agent_name: str, kind: DeepAgentKind) -> CompiledStateGraph:
        """读端 不加锁 dict.get 在 CPython GIL 下是原子的 仅用于系统级实例"""
        key = f"{agent_name}-{kind}"
        inst = self._instances.get(key)
        if inst is None:
            raise KeyError(f"deep_agent not found: {key}")
        return inst

    async def get_or_build(
        self, agent_name: str, kind: DeepAgentKind, *, owner_user_id: str | None = None
    ) -> CompiledStateGraph:
        """按用户获取或懒构建 agent 实例

        - owner_user_id 为 None: 返回系统级实例 (同 get)
        - 有值: 缓存 key 为 {user_id}:{name}-{kind}
          版本过期则重新构建 MCP/Skills 按用户加载
        """
        if owner_user_id is None:
            return self.get(agent_name, kind)

        key = f"{owner_user_id}:{agent_name}-{kind}"
        cached_version = self._versions.get(key, -1)
        if cached_version == self._global_version:
            inst = self._instances.get(key)
            if inst is not None:
                return inst

        # 需要构建: 从 DB 拉 agent 配置
        record = await self._storage.get_agent(agent_name, owner_user_id=owner_user_id)
        if record is None:
            # 用户可能看不到该 agent (不属于自己且非 system)
            # 降级: 尝试系统级实例
            _logger.warning("get_or_build 降级到系统级实例", agent_name=agent_name, owner_user_id=owner_user_id)
            return self.get(agent_name, kind)

        inst = await _build_one(
            record, kind, self._settings,
            storage=self._storage,
            owner_user_id=owner_user_id,
        )
        async with self._lock:
            self._instances[key] = inst
            self._versions[key] = self._global_version
        _logger.info("get_or_build 按用户构建", key=key)
        return inst

    async def build_thinking_reply(self, agent_name: str, *, owner_user_id: str | None = None) -> CompiledStateGraph:
        """临时构建一个开启 thinking 模式的 reply deep_agent  本轮一次性使用

        owner_user_id 用于按用户加载 MCP/Skills
        """
        from ..storage.mongo import MotorMongoStorage

        if owner_user_id is None:
            raise RuntimeError("build_thinking_reply 需要 owner_user_id 才能拉 agent 配置")
        record = await self._storage.get_agent(agent_name, owner_user_id=owner_user_id)
        if record is None:
            raise KeyError(f"agent 不存在 name={agent_name}")
        return await _build_one(
            record,
            "reply",
            self._settings,
            storage=self._storage,
            thinking_enabled=True,
            owner_user_id=owner_user_id,
        )

    async def reload_all(self, *, owner_user_id: str | None = None) -> int:
        """重载 agent 实例缓存 使 skills 或 MCP 配置变更生效

        - owner_user_id 有值: 只清除该用户的缓存 下次请求 get_or_build 自动重建
        - owner_user_id 为 None: 清除所有用户缓存 (全局重载场景)
        系统级实例(无 MCP/Skills)不需要重建
        返回当前注册的 agent 数量
        """
        async with self._lock:
            self._global_version += 1
            if owner_user_id is not None:
                # 只清除指定用户的缓存 条目
                stale_keys = [k for k in self._instances if k.startswith(f"{owner_user_id}:")]
            else:
                # 全局: 清除所有按用户缓存 条目
                stale_keys = [k for k in self._instances if ":" in k]
            for k in stale_keys:
                self._instances.pop(k, None)
                self._versions.pop(k, None)
        count = len(self.names())
        _logger.info("deep_agents 版本递增 缓存已清除", new_version=self._global_version, owner_user_id=owner_user_id, agent_count=count)
        return count

    def names(self) -> list[str]:
        """返回所有已注册 agent 的名字 去重排序"""
        return sorted({k.rsplit("-", 1)[0] for k in self._instances.keys()})


def build_registry(settings: Settings, storage: Any | None = None) -> DeepAgentRegistry:
    """工厂函数 用 settings 和可选的 storage 创建 registry

    storage 为 None 时不加载 MCP 工具(用于测试环境)
    """
    return DeepAgentRegistry(settings, storage=storage)

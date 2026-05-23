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
from typing import Any, Literal

import structlog
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
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


async def _build_one(
    agent_record: AgentRecord,
    kind: DeepAgentKind,
    settings: Settings,
    *,
    storage: Any | None = None,
) -> CompiledStateGraph:
    """根据 agent 配置构造一个 deep_agent 实例

    think 模式 system_prompt 强约束 不能调工具
    reply 模式 system_prompt 鼓励规划 + 工具调用 同时打开 streaming

    base_url + api_key 直接来自 agent_record  不再依赖外部 profile
    settings 仅提供 runtime.http_timeout_seconds 等运行时参数

    reply 模式工具加载顺序: 先加载内置 3 个共享 tool 再加载 MCP 工具
    若 storage 传入则从数据库读取 mcp_config 文档动态加载
    """
    suffix = THINK_SYSTEM_SUFFIX if kind == "think" else REPLY_SYSTEM_SUFFIX
    system_prompt = agent_record.prompt + suffix
    # ChatOpenAI 仅在创建时拿凭据 真正网络调用发生在 ainvoke/astream 时
    # 因此这里多次重建实例不会发起网络请求 测试可以放心调用
    model = ChatOpenAI(
        model=agent_record.model,
        api_key=agent_record.api_key,
        base_url=agent_record.base_url,
        streaming=(kind == "reply"),
        timeout=settings.runtime.http_timeout_seconds,
        max_retries=1,
    )

    # 挂工具策略
    #   reply 模式 注入共享 tool(current_time/http_get/web_search) + MCP 工具
    #   think 模式 显式空 prompt 已禁止调工具 也不挂 tool 防 LLM 误判
    mcp_servers: list[str] = []
    mcp_tool_count = 0
    if kind == "reply":
        from .tools import get_shared_tools, load_mcp_tools_from_db

        tools = get_shared_tools()
        # MCP 工具加载 每个 server 失败独立容错 不阻塞 agent 初始化
        if storage is not None:
            mcp_tools, mcp_servers = await load_mcp_tools_from_db(storage)
            mcp_tool_count = len(mcp_tools)
            if mcp_tools:
                tools = [*tools, *mcp_tools]
    else:
        tools = []

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
        # key 格式 {agent_name}-{kind} value 是 deepagents 编译后图
        self._instances: dict[str, CompiledStateGraph] = {}
        self._lock = asyncio.Lock()

    async def initialize(self, records: list[AgentRecord]) -> None:
        """启动时一次性 build 所有实例 任意数量都接受 包含 0 条

        records 为空 names() 返回空列表 后续可通过 reload 动态新增
        """
        new_inst: dict[str, CompiledStateGraph] = {}
        for r in records:
            new_inst[f"{r.name}-think"] = await _build_one(
                r, "think", self._settings, storage=self._storage
            )
            new_inst[f"{r.name}-reply"] = await _build_one(
                r, "reply", self._settings, storage=self._storage
            )
        # 替换整张表 锁内只做指针赋值 持锁时间极短
        async with self._lock:
            self._instances = new_inst
        _logger.info("deep_agents 初始化完成", count=len(new_inst))

    async def reload(self, record: AgentRecord) -> None:
        """热替换 / 新增某个 agent 的 2 个实例

        先在锁外 build 失败也不影响现有实例 只在最终 swap 时持锁
        若该 agent 此前不在表中 等同于追加新数字员工
        """
        new_think = await _build_one(
            record, "think", self._settings, storage=self._storage
        )
        new_reply = await _build_one(
            record, "reply", self._settings, storage=self._storage
        )
        async with self._lock:
            self._instances[f"{record.name}-think"] = new_think
            self._instances[f"{record.name}-reply"] = new_reply
        _logger.info("deep_agents 热替换", name=record.name, version=record.version)

    def unregister(self, name: str) -> None:
        """删除 agent 时同步移除其 think+reply 实例

        说明:
            - 这是同步方法 因为 dict.pop 在 GIL 下原子 不需要 lock 来保证一致性
            - 后续如有并发 reload(name) 与 unregister(name) 竞态 由调用方串行
              路由层 delete 与 update 互斥 不会触发该竞态
        """
        self._instances.pop(f"{name}-think", None)
        self._instances.pop(f"{name}-reply", None)
        _logger.info("deep_agents 注销", name=name)

    def get(self, agent_name: str, kind: DeepAgentKind) -> CompiledStateGraph:
        """读端 不加锁 dict.get 在 CPython GIL 下是原子的"""
        key = f"{agent_name}-{kind}"
        inst = self._instances.get(key)
        if inst is None:
            raise KeyError(f"deep_agent not found: {key}")
        return inst

    def names(self) -> list[str]:
        """返回所有已注册 agent 的名字 去重排序"""
        return sorted({k.rsplit("-", 1)[0] for k in self._instances.keys()})


def build_registry(settings: Settings, storage: Any | None = None) -> DeepAgentRegistry:
    """工厂函数 用 settings 和可选的 storage 创建 registry

    storage 为 None 时不加载 MCP 工具(用于测试环境)
    """
    return DeepAgentRegistry(settings, storage=storage)

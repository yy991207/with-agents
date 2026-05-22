"""deepagents 实例构建器 提供 think + reply 双实例预创建与热替换能力

预创建策略:
    每个 agent 同时构造两个 deep_agent 实例
    - {agent_name}-think: system_prompt 强约束 50 字以内 不调用任何工具
    - {agent_name}-reply: 完整能力 鼓励规划+工具调用 流式回复
    4 个 agent 共 8 个实例 启动时 initialize 一次 之后改 agent 配置走 reload

并发安全:
    - 读端不加锁 dict.get 是 GIL 内原子操作 替换字典 value 也是原子的
    - 写端用 asyncio.Lock 串行化 避免两次 reload 中间态被并发读到 1 新 1 旧
    - 实例 build 在锁外 仅最终 swap 写入 dict 在锁内 减少锁持有时间
    - registry 与底层 LLM 客户端均为异步对象 必须在使用它的事件循环中创建
      谁创建谁使用 不可在不同 loop 间复用 详见全局规范
"""

from __future__ import annotations

import asyncio
from typing import Literal

import structlog
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from ..config import Settings
from ..core.models import AgentRecord, ProviderProfile

_logger = structlog.get_logger(__name__)

# 实例 kind 枚举 think 用于 50 字理由 reply 用于完整作答
DeepAgentKind = Literal["think", "reply"]


# think 模式系统提示后缀 强约束输出长度且禁止工具调用
THINK_SYSTEM_SUFFIX = """

[当前任务] 用户提了一个问题 你需要在 50 个汉字以内说一句话
解释你为什么适合回答这个问题
硬约束
 1 输出不超过 50 个汉字
 2 严禁调用任何工具(包括 write_todos read_file write_file 等)
 3 严禁分点列项 直接一句话
 4 不要重复或转述用户的问题
"""


# reply 模式系统提示后缀 鼓励规划+工具调用
REPLY_SYSTEM_SUFFIX = """

[当前任务] 用户给你一个具体可执行的任务 请使用工具完成它
1 必要时使用 write_todos 写下计划
2 使用 read_file 读取相关文件
3 在内存中处理
4 使用 write_file 写出结果文件(如有需要)
5 完成后用一句话告诉用户做了什么
全程使用工具操作 不要把整段文件内容贴回
"""


def _build_one(
    agent_record: AgentRecord,
    kind: DeepAgentKind,
    profile: ProviderProfile,
    settings: Settings,
) -> CompiledStateGraph:
    """根据 agent 配置 + 关联 profile 构造一个 deep_agent 实例

    think 模式 system_prompt 强约束 不能调工具
    reply 模式 system_prompt 鼓励规划 + 工具调用 同时打开 streaming

    base_url + api_key 不再来自 settings 顶层 而来自 record 引用的 profile
    settings 仅提供 runtime.http_timeout_seconds 等运行时参数
    """
    suffix = THINK_SYSTEM_SUFFIX if kind == "think" else REPLY_SYSTEM_SUFFIX
    system_prompt = agent_record.prompt + suffix
    # ChatOpenAI 仅在创建时拿凭据 真正网络调用发生在 ainvoke/astream 时
    # 因此这里多次重建实例不会发起网络请求 测试可以放心调用
    model = ChatOpenAI(
        model=agent_record.model,
        api_key=profile.api_key,
        base_url=profile.base_url,
        streaming=(kind == "reply"),
        timeout=settings.runtime.http_timeout_seconds,
        max_retries=1,
    )

    # 挂工具策略
    #   reply 模式 注入共享 tool(current_time/http_get/web_search 等)
    #   think 模式 显式空 prompt 已禁止调工具 也不挂 tool 防 LLM 误判
    if kind == "reply":
        from .tools import get_shared_tools

        tools = get_shared_tools()
    else:
        tools = []

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        name=f"{agent_record.name}-{kind}",
    )


class DeepAgentRegistry:
    """4 个 agent 各自 think + reply 共 8 个实例的注册表

    支持热替换 改了某个 agent 配置后调 reload(record, profile) 原子 swap 进字典
    读端不加锁 写端用 asyncio.Lock 串行化
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # key 格式 {agent_name}-{kind} value 是 deepagents 编译后图
        self._instances: dict[str, CompiledStateGraph] = {}
        self._lock = asyncio.Lock()

    async def initialize(
        self,
        records: list[AgentRecord],
        profiles_by_name: dict[str, ProviderProfile],
    ) -> None:
        """启动时一次性 build 全部 8 个实例

        records 必须恰好 4 条 不允许半启动状态 直接抛 ValueError
        profiles_by_name 必须包含每条 record.profile_name 对应的 profile
            缺则抛 KeyError 让启动早暴露
        """
        if len(records) != 4:
            raise ValueError(f"expected 4 agents got {len(records)}")
        new_inst: dict[str, CompiledStateGraph] = {}
        for r in records:
            profile = profiles_by_name.get(r.profile_name)
            if profile is None:
                raise KeyError(
                    f"agent {r.name} 引用的 profile {r.profile_name} 不存在 请先创建 profile"
                )
            new_inst[f"{r.name}-think"] = _build_one(r, "think", profile, self._settings)
            new_inst[f"{r.name}-reply"] = _build_one(r, "reply", profile, self._settings)
        # 替换整张表 锁内只做指针赋值 持锁时间极短
        async with self._lock:
            self._instances = new_inst
        _logger.info("deep_agents 初始化完成", count=len(new_inst))

    async def reload(self, record: AgentRecord, profile: ProviderProfile) -> None:
        """热替换某个 agent 的 2 个实例

        先在锁外 build 失败也不影响现有实例 只在最终 swap 时持锁
        必须传入对应的 profile 由调用方保证 profile.name == record.profile_name
        """
        new_think = _build_one(record, "think", profile, self._settings)
        new_reply = _build_one(record, "reply", profile, self._settings)
        async with self._lock:
            self._instances[f"{record.name}-think"] = new_think
            self._instances[f"{record.name}-reply"] = new_reply
        _logger.info("deep_agents 热替换", name=record.name, version=record.version)

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


def build_registry(settings: Settings) -> DeepAgentRegistry:
    """工厂函数 用 settings 创建一个空 registry 调用方再 await initialize(records)"""
    return DeepAgentRegistry(settings)

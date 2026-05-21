"""共享 tool 集合骨架

设计要点:
    - tool 是无状态函数 通过 LangChain 的 RunnableConfig 把 task_id 与 agent_name
      注入到调用上下文 用于在 SSE 与日志中做隔离
    - tool 之间不共享可变全局状态 涉及 IO 时通过依赖注入获取 client
    - 后续会引入 web_search file_read execute_python 等业务 tool

当前 M1 阶段仅提供占位的导出列表与一个示意 tool 真实实现在 M2 阶段补齐
"""

from __future__ import annotations

from typing import Any


def get_shared_tools() -> list[Any]:
    """返回供 reply 阶段 deepagents 挂载的共享 tool 列表"""

    raise NotImplementedError("M2 实施")


def example_placeholder_tool() -> str:
    """占位示例 真实 tool 在 M2 阶段补齐"""

    raise NotImplementedError("M2 实施")

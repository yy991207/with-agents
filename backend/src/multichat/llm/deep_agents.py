"""deepagents 实例构建器骨架

预创建策略:
    - 4 个 think 用轻量 agent 仅做 50 字以内发言理由 不挂工具或挂极简工具
    - 4 个 reply 用完整 agent 挂全部业务工具 用于深度规划与多步执行
    - 合计 8 个实例 在应用启动时构建并注入 TaskManager

注意:deepagents 的图与底层 LLM 客户端均为异步对象
要求"谁创建谁使用" 不可在不同 event loop 间复用 详见全局规范

当前为 M1 骨架 真实构建逻辑在 M2 阶段补齐
"""

from __future__ import annotations

from typing import Any


def build_deep_agents(settings: Any) -> dict[str, Any]:
    """根据配置预创建 8 个 deepagents 实例

    返回结构:
        {
            "think": {agent_name: graph, ...},
            "reply": {agent_name: graph, ...},
        }
    """

    raise NotImplementedError("M2 实施")

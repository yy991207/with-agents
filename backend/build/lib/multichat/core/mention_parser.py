"""mention 解析器骨架

业务场景:用户消息中可能用 @agent_name 直接指定某个参与者发言
而非走完整的 think-then-choose 流程

parse_single_mention 应该:
    - 从原始消息中识别第一个有效 @mention
    - 校验是否在已配置 agents 中
    - 返回 mention 命中的 agent 名以及剥离 mention 后的纯净文本

当前为 M1 骨架 真实正则与边界处理在 M2 阶段补齐
"""

from __future__ import annotations


class ParserUnimplementedError(NotImplementedError):
    """显式标识 mention 解析尚未实施 测试用"""


def parse_single_mention(
    text: str, valid_agents: list[str] | None = None
) -> tuple[str | None, str]:
    """从文本中解析单个 @mention

    参数:
        text: 用户原始消息
        valid_agents: 当前合法的 agent 名单 用于校验 mention 是否合法

    返回:
        (命中的 agent 名 或 None, 去除 mention 后的纯文本)
    """

    raise ParserUnimplementedError("M2 实施")

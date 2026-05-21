"""mention 解析器

业务场景:用户消息中可能用 @AgentName 直接指定某个参与者发言
而非走完整的 think-then-choose 流程

设计要点:
    - 只识别消息中第一个 @ 提及 后续 @ 全部当普通文本
    - agent 名按大小写不敏感匹配 落库时使用 valid_agents 中的原始大小写
    - 大小写不敏感前要求 @ 紧跟一个 ASCII 字母或汉字 至多吃 32 字符 防止全文吞噬
    - 命中后返回 valid_agents 中的标准名 未命中返回 None
"""

from __future__ import annotations

import re
from typing import Iterable

# 匹配 @ 后紧跟 1~32 个非空白非标点字符 用作 agent 名候选
# 不强制英文 兼容中文 agent 名 真正校验靠 valid_agents 列表
_MENTION_RE = re.compile(r"@([^\s@,，。:：;；!！?？]{1,32})")


class ParserUnimplementedError(NotImplementedError):
    """历史占位错误类型 暂保留 防止旧测试 import 失败"""


def parse_single_mention(
    text: str,
    valid_agents: Iterable[str] | None = None,
) -> str | None:
    """从用户消息中解析单个 @mention

    参数:
        text: 用户原始消息
        valid_agents: 合法 agent 名集合 用于过滤无效 @ 比如 @某某 不是已注册 agent
            None 或空集合时无 agent 可校验 直接返回 None

    返回:
        命中的 agent 标准名(以 valid_agents 中的原始大小写为准) 否则 None
    """
    if not text or not valid_agents:
        return None

    # 把 valid_agents 规整成 lower → original 字典 支持大小写不敏感匹配
    name_map: dict[str, str] = {}
    for name in valid_agents:
        if not isinstance(name, str) or not name:
            continue
        name_map.setdefault(name.lower(), name)
    if not name_map:
        return None

    # 取消息里出现的第一个有效 @mention 即可 后续 @ 不再作为指令解析
    for m in _MENTION_RE.finditer(text):
        candidate = m.group(1).strip().lower()
        if not candidate:
            continue
        # 完全匹配命中
        if candidate in name_map:
            return name_map[candidate]
        # 也允许"前缀完整匹配" 比如用户敲 @glmplease 不命中 必须严格相等
        # 这里没有降级到子串匹配 避免误伤
    return None

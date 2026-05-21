"""mention 解析占位测试 真实用例在 M2 阶段补齐"""

from __future__ import annotations

import pytest

from multichat.core.mention_parser import ParserUnimplementedError, parse_single_mention


def test_parser_not_implemented_yet() -> None:
    """当前骨架阶段 parse_single_mention 应抛 ParserUnimplementedError"""
    with pytest.raises(ParserUnimplementedError):
        parse_single_mention("@deepseek 你好", valid_agents=["deepseek"])

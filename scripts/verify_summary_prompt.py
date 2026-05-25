"""红绿灯  校验 summarization.py 的 prompt 常量

跑法
    conda activate multi-chat
    python scripts/verify_summary_prompt.py

验证三件事
    1 import summarization 不报错 SESSION_SUMMARY_SYSTEM_PROMPT 是非空字符串
    2 doc 文件中"二、模板正文"代码块的内容与常量字面值完全一致
       一个字也不能差  字数限制必须是 2000
    3 SESSION_SUMMARY_HUMAN_TEMPLATE.format 能正常拼出预期文本
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from multichat.llm.summarization import (  # noqa: E402
    SESSION_SUMMARY_HUMAN_TEMPLATE,
    SESSION_SUMMARY_MAX_CHARS,
    SESSION_SUMMARY_SYSTEM_PROMPT,
)


DOC_PATH = ROOT / "doc" / "会话摘要 Prompt 模板.md"


def case_const_basic() -> None:
    """case 1 常量本身基本合法"""
    assert isinstance(SESSION_SUMMARY_SYSTEM_PROMPT, str)
    assert len(SESSION_SUMMARY_SYSTEM_PROMPT) > 100, "system prompt 太短  落地有问题"
    assert SESSION_SUMMARY_MAX_CHARS == 2000
    # 关键词必须出现 防止后续误改
    must_have = [
        "会话摘要助手",
        "总长度不超过 2000 字",
        "### 1. 会话目标",
        "### 7. 风险与坑",
    ]
    for kw in must_have:
        assert kw in SESSION_SUMMARY_SYSTEM_PROMPT, f"system prompt 缺关键字段 {kw}"
    print("case 1 PASS  常量结构与字数上限就位")


def case_doc_aligned() -> None:
    """case 2 doc 文件中 二、模板正文 代码块内容必须与常量逐字一致"""
    text = DOC_PATH.read_text(encoding="utf-8")
    # 提取 "## 二、模板正文" 之后第一个 ``` 包围块
    m = re.search(
        r"##\s*二、模板正文.*?```(.*?)```",
        text,
        flags=re.DOTALL,
    )
    assert m, "doc 中没找到 二、模板正文 代码块"
    doc_body = m.group(1).strip("\n")
    const_body = SESSION_SUMMARY_SYSTEM_PROMPT.strip("\n")
    if doc_body != const_body:
        # 输出 diff 方便定位
        from difflib import unified_diff
        diff = "\n".join(
            unified_diff(
                doc_body.splitlines(),
                const_body.splitlines(),
                fromfile="doc",
                tofile="const",
                lineterm="",
            )
        )
        raise AssertionError(f"doc 与常量内容不一致 diff:\n{diff}")
    # 字数限制不能写错成 1500
    assert "1500" not in doc_body, "doc 仍残留 1500 字限制 应改成 2000"
    print("case 2 PASS  doc 与常量逐字一致 字数限制 2000")


def case_human_template() -> None:
    """case 3 human 模板能正常拼接"""
    rendered = SESSION_SUMMARY_HUMAN_TEMPLATE.format(
        old_summary="(无)",
        conversation="用户: 你好\n助手: 在",
    )
    assert "[上一版摘要]" in rendered
    assert "[待压缩对话]" in rendered
    assert "(无)" in rendered
    assert "用户: 你好" in rendered
    print("case 3 PASS  human 模板拼接正常")


def main() -> None:
    case_const_basic()
    case_doc_aligned()
    case_human_template()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    main()

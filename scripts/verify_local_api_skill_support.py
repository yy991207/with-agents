"""本机 API skill 支持红绿灯  静态校验 localhost 调用能力已接通

跑法
    conda activate deepagent
    python scripts/verify_local_api_skill_support.py

case
    1 tools.py 存在 local_api_call 工具
    2 local_api_call 白名单包含 /api/skills /api/mcp 与会话历史相关路径
    3 http_get 不再拒绝 localhost / 127.0.0.1
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "backend" / "src" / "multichat" / "llm" / "tools.py"


def case1_should_have_local_api_call_tool() -> None:
    text = TOOLS.read_text(encoding="utf-8")
    assert "async def local_api_call(" in text, "missing local_api_call"
    print("case 1 PASS  已存在 local_api_call 工具")


def case2_should_whitelist_required_local_paths() -> None:
    text = TOOLS.read_text(encoding="utf-8")
    required = [
        "/api/skills",
        "/api/mcp",
        "/history/",
        "/sessions",
    ]
    missing = [item for item in required if item not in text]
    assert not missing, f"missing whitelist items: {missing}"
    print("case 2 PASS  本机接口白名单已覆盖 skills/mcp/会话历史")


def case3_http_get_should_not_block_localhost() -> None:
    text = TOOLS.read_text(encoding="utf-8")
    assert 'host in {"localhost", "metadata.google.internal", "169.254.169.254"}' not in text
    assert "return ip.is_private or ip.is_loopback or ip.is_link_local" not in text
    print("case 3 PASS  http_get 不再拦截 localhost 和本机回环地址")


def main() -> None:
    case1_should_have_local_api_call_tool()
    case2_should_whitelist_required_local_paths()
    case3_http_get_should_not_block_localhost()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    main()

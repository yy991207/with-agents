"""红绿灯验证脚本 校验 max_input_tokens 字段在三条路径上的行为

执行方式
    conda activate multi-chat
    python scripts/verify_max_input_tokens.py

验证三件事
    1 seed 路径   _agent_doc_to_record 读种子文档 缺字段时兜底 200000 不抛
    2 用户路径   _normalize_models 强校验 缺字段直接 ValueError
    3 用户路径   _normalize_models 接受 ModelCatalogEntry 实例往返一致

不依赖真实 mongo 直接构造 dict 调函数 关注纯逻辑
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from multichat.core.models import ModelCatalogEntry  # noqa: E402
from multichat.storage.mongo import (  # noqa: E402
    _agent_doc_to_record,
    _normalize_models,
)


def case_seed_legacy_doc_no_tokens() -> None:
    """case 1: 种子文档 / 老库文档 没 max_input_tokens 字段 也得能正常加载"""
    legacy_doc = {
        "name": "Kimi",
        "display_name": "Kimi",
        "provider_type": "openai_compatible",
        "base_url": "https://x",
        "api_key": "k",
        "model": "kimi-k2",
        "available_models": [
            {"model_id": "kimi-k2", "label": "kimi-k2"},
            {"model_id": "kimi-128k", "label": "kimi-128k"},
        ],
        "prompt": "p",
        "version": 1,
    }
    rec = _agent_doc_to_record(legacy_doc)
    assert len(rec.available_models) == 2
    for m in rec.available_models:
        assert m.max_input_tokens == 200000, f"兜底应为 200000 实际 {m.max_input_tokens}"
    print("case 1 PASS  老数据缺字段读取兜底 200000")


def case_user_submit_missing_tokens() -> None:
    """case 2: 用户路径 dict 里没 max_input_tokens 必须抛 ValueError"""
    bad_models = [{"model_id": "qwen-plus", "label": "qwen-plus"}]
    try:
        _normalize_models(bad_models)
    except ValueError as exc:
        print(f"case 2 PASS  用户路径缺字段抛 ValueError: {exc}")
        return
    raise AssertionError("case 2 FAIL  用户路径缺字段未抛 ValueError")


def case_user_submit_zero_tokens() -> None:
    """case 3: 用户路径 max_input_tokens=0 也抛"""
    bad_models = [
        {"model_id": "qwen-plus", "label": "qwen-plus", "max_input_tokens": 0}
    ]
    try:
        _normalize_models(bad_models)
    except ValueError as exc:
        print(f"case 3 PASS  用户路径 0 值抛 ValueError: {exc}")
        return
    raise AssertionError("case 3 FAIL  0 值未抛")


def case_user_submit_valid() -> None:
    """case 4: 用户路径 ModelCatalogEntry 实例与 dict 都接受 字段保留"""
    via_entry = _normalize_models([
        ModelCatalogEntry(model_id="qwen-plus", label="qwen-plus", max_input_tokens=131072)
    ])
    assert via_entry == [
        {"model_id": "qwen-plus", "label": "qwen-plus", "max_input_tokens": 131072}
    ], via_entry

    via_dict = _normalize_models([
        {"model_id": "kimi-k2", "label": "kimi-k2", "max_input_tokens": 131072}
    ])
    assert via_dict == [
        {"model_id": "kimi-k2", "label": "kimi-k2", "max_input_tokens": 131072}
    ], via_dict
    print("case 4 PASS  用户路径合法值往返一致")


def main() -> None:
    case_seed_legacy_doc_no_tokens()
    case_user_submit_missing_tokens()
    case_user_submit_zero_tokens()
    case_user_submit_valid()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    main()

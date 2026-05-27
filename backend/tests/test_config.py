"""config 模块单元测试

覆盖:
    - 正常 yaml 加载 顶层字段对齐
    - 缺字段抛 ValidationError
    - judge.agent 不在 agents 段抛错
    - 入参 path 优先级高于环境变量
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from multichat.config import Settings, load_settings

# 一份完整最小 yaml 用于多个用例复用
_FULL_YAML: dict = {
    "key": "sk-test-1234",
    "base_url": "https://example.com/v1",
    "agents": {
        "DeepSeek": {
            "model": "deepseek-test",
            "prompt": "你是 DeepSeek",
        },
        "GLM": {
            "model": "glm-test",
            "prompt": "你是 GLM",
        },
    },
    "judge": {
        "agent": "GLM",
        "prompt": "你是裁判",
    },
    "mongo": {
        "uri": "mongodb://localhost:27017",
        "db": "multi_chat_test",
    },
    "runtime": {
        "history_max_rounds": 5,
        "reply_flush_interval_ms": 100,
        "http_timeout_seconds": 10,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8888,
        "cors_origins": ["http://localhost:5173"],
    },
    "auth": {
        "session_cookie_name": "multi_chat_session",
        "session_ttl_hours": 168,
        "password_pepper": "test-pepper",
        "session_cookie_secure": False,
    },
    "minio": {
        "endpoint": "localhost:9000",
        "access_key": "minioadmin",
        "secret_key": "minioadmin",
        "bucket": "multi-chat",
        "secure": False,
    },
}


def _write_yaml(tmp_path: Path, payload: dict) -> Path:
    """工具方法 把 dict 落到临时 yaml 返回路径"""
    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return target


def test_load_settings_full(tmp_path: Path) -> None:
    """完整 yaml 能正常加载 字段值精确对齐"""
    cfg = _write_yaml(tmp_path, _FULL_YAML)
    settings = load_settings(cfg)

    assert isinstance(settings, Settings)
    assert settings.key == "sk-test-1234"
    assert settings.base_url.endswith("/v1")
    assert set(settings.agents.keys()) == {"DeepSeek", "GLM"}
    assert settings.agents["DeepSeek"].model == "deepseek-test"
    assert settings.judge.agent == "GLM"
    assert settings.mongo.db == "multi_chat_test"
    assert settings.runtime.history_max_rounds == 5
    assert settings.server.port == 8888
    assert settings.server.cors_origins == ["http://localhost:5173"]
    assert settings.auth.session_cookie_name == "multi_chat_session"
    assert settings.auth.session_ttl_hours == 168
    assert settings.minio is not None
    assert settings.minio.bucket == "multi-chat"


def test_load_settings_missing_required_field(tmp_path: Path) -> None:
    """缺顶层必填字段直接 ValidationError 早暴露"""
    bad = {k: v for k, v in _FULL_YAML.items() if k not in ("key", "base_url")}
    cfg = _write_yaml(tmp_path, bad)
    with pytest.raises(ValidationError):
        load_settings(cfg)


def test_load_settings_judge_agent_not_in_agents(tmp_path: Path) -> None:
    """judge.agent 必须在 agents 段中 否则 ValidationError"""
    bad = {**_FULL_YAML, "judge": {"agent": "Mystery", "prompt": "x"}}
    cfg = _write_yaml(tmp_path, bad)
    with pytest.raises(ValidationError) as exc_info:
        load_settings(cfg)
    assert "Mystery" in str(exc_info.value)


def test_load_settings_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 path 优先于 MULTICHAT_CONFIG 环境变量"""
    real = _write_yaml(tmp_path, _FULL_YAML)
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    fake_path = fake_dir / "config.yaml"
    fake_path.write_text("not-a-mapping", encoding="utf-8")

    monkeypatch.setenv("MULTICHAT_CONFIG", str(fake_path))
    # 显式入参应优先 不读到环境变量指向的烂文件
    settings = load_settings(real)
    assert settings.judge.agent == "GLM"


def test_load_settings_via_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 path 时 走 MULTICHAT_CONFIG"""
    cfg = _write_yaml(tmp_path, _FULL_YAML)
    monkeypatch.setenv("MULTICHAT_CONFIG", str(cfg))
    settings = load_settings()
    assert settings.judge.agent == "GLM"


def test_load_settings_missing_file(tmp_path: Path) -> None:
    """指定的 yaml 文件不存在 抛 FileNotFoundError"""
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.yaml")


def test_load_settings_root_not_mapping(tmp_path: Path) -> None:
    """yaml 根节点不是 mapping 抛 ValueError"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(cfg)


def test_load_settings_with_auth_and_minio_sections(tmp_path: Path) -> None:
    """auth 和 minio 段应能被精确加载"""
    cfg = _write_yaml(tmp_path, _FULL_YAML)
    settings = load_settings(cfg)

    assert settings.auth.session_cookie_name == "multi_chat_session"
    assert settings.auth.password_pepper == "test-pepper"
    assert settings.minio is not None
    assert settings.minio.endpoint == "localhost:9000"
    assert settings.minio.bucket == "multi-chat"

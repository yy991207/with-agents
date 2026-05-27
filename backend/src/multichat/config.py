"""配置加载模块 基于 pydantic-settings + PyYAML

字段对齐项目根 config.example.yaml 主要包含:
    - key/base_url: LLM 服务凭据与地址
    - agents: 4 个参与者 LLM 的种子配置 名称 模型 prompt
        注意 运行时不再读这一段 仅作为首次启动 seed 注入到 MongoDB 之用
    - judge: 评判 agent 指针 仅作为 yaml 种子默认值 运行时以 DB 中 settings 集合为准
    - mongo: MongoDB 连接信息
    - runtime: 运行时调优参数
    - server: HTTP 服务监听端口与跨域设置

加载顺序:
    1. 入参 path 显式指定
    2. 环境变量 MULTICHAT_CONFIG
    3. backend/../config.yaml 默认相对路径
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 启动日志 用于打印加载结果
_logger = structlog.get_logger(__name__)


class AgentConfig(BaseModel):
    """单个参与者 LLM 的种子配置

    yaml 中 agents 段的子项 仅用作 DB 首次 seed 之用
    运行时所有 agent 配置从 MongoDB 读取
    """

    model: str
    prompt: str


class JudgeConfig(BaseModel):
    """裁判段配置 仅作 yaml 种子默认值

    agent 字段必须出现在同 yaml 的 agents 段中 校验在 Settings 上层完成
    """

    agent: str
    prompt: str


class MongoConfig(BaseModel):
    """MongoDB 连接配置"""

    uri: str = "mongodb://localhost:27017"
    db: str = "multi_chat"


class RuntimeConfig(BaseModel):
    """运行时调优参数 与 yaml runtime 段对齐"""

    history_max_rounds: int = 10
    reply_flush_interval_ms: int = 200
    http_timeout_seconds: int = 30
    # 当前允许模型读写的项目内文档目录 相对项目根路径配置
    # 运行时会映射成 deepagents 的虚拟绝对路径 例如 "doc" -> "/doc"
    document_roots: list[str] = Field(default_factory=lambda: ["doc", "docs"])
    # 仓库外的本地文档挂载点 例如桌面/文稿
    # name 是模型看到的虚拟目录名 path 是宿主机真实路径(支持 ~/)
    external_document_mounts: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("document_roots", mode="before")
    @classmethod
    def _normalize_document_roots(cls, value: Any) -> list[str]:
        """把文档目录统一规整成相对项目根的 posix 路径

        约束:
            - 必须是字符串列表
            - 只允许相对路径 不允许绝对路径
            - 禁止出现 .. 和 ~ 避免目录逃逸
        """
        if value is None:
            return ["doc", "docs"]
        if not isinstance(value, list):
            raise ValueError("runtime.document_roots 必须是字符串列表")

        normalized: list[str] = []
        for raw in value:
            if not isinstance(raw, str):
                raise ValueError("runtime.document_roots 里的每一项都必须是字符串")
            cleaned = raw.strip().replace("\\", "/")
            if not cleaned:
                continue
            path = PurePosixPath(cleaned)
            if path.is_absolute():
                raise ValueError(
                    f"runtime.document_roots 只允许相对路径 收到绝对路径 {raw!r}"
                )
            if ".." in path.parts:
                raise ValueError(
                    f"runtime.document_roots 不允许包含 .. 收到 {raw!r}"
                )
            if "~" in path.parts:
                raise ValueError(
                    f"runtime.document_roots 不允许包含 ~ 收到 {raw!r}"
                )
            posix = path.as_posix()
            if posix in ("", "."):
                continue
            normalized.append(posix)

        deduped = list(dict.fromkeys(normalized))
        return deduped or ["doc", "docs"]

    @field_validator("external_document_mounts", mode="before")
    @classmethod
    def _normalize_external_document_mounts(
        cls,
        value: Any,
    ) -> list[dict[str, str]]:
        """规整仓库外文档挂载点

        约束:
            - name 只允许字母数字下划线中划线
            - path 必须是绝对路径或 ~/ 开头
            - 不允许重复 name
        """
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("runtime.external_document_mounts 必须是对象列表")

        normalized: list[dict[str, str]] = []
        seen_names: set[str] = set()
        for raw in value:
            if not isinstance(raw, dict):
                raise ValueError("runtime.external_document_mounts 里的每一项都必须是对象")
            name = str(raw.get("name", "")).strip()
            path = str(raw.get("path", "")).strip()
            if not name or not path:
                raise ValueError("runtime.external_document_mounts 的 name 和 path 都不能为空")
            safe_name = name.replace("\\", "/").strip("/").strip()
            if not safe_name:
                raise ValueError("runtime.external_document_mounts.name 不能为空")
            if safe_name in seen_names:
                raise ValueError(f"runtime.external_document_mounts.name 重复 {safe_name!r}")
            allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
            if any(ch not in allowed_chars for ch in safe_name):
                raise ValueError(
                    f"runtime.external_document_mounts.name 只能包含字母数字下划线中划线 收到 {safe_name!r}"
                )
            if not (path.startswith("~/") or path.startswith("/")):
                raise ValueError(
                    f"runtime.external_document_mounts.path 必须是绝对路径或 ~/ 开头 收到 {path!r}"
                )
            normalized.append({"name": safe_name, "path": path})
            seen_names.add(safe_name)
        return normalized


class ServerConfig(BaseModel):
    """HTTP 服务监听端口与跨域设置"""

    host: str = "0.0.0.0"
    port: int = 8002
    cors_origins: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    """应用总配置 顶层字段直接对齐 yaml 7 个 key"""

    model_config = SettingsConfigDict(
        env_prefix="MULTICHAT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # 以下 4 项现在只用于"首次空库 seed"兼容老流程
    # 运行时 agent 配置与 judge 指针都从 MongoDB 读取
    key: str | None = None
    base_url: str | None = None
    agents: dict[str, AgentConfig] | None = None
    judge: JudgeConfig | None = None
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @model_validator(mode="after")
    def _validate_judge_in_agents(self) -> "Settings":
        """yaml 种子合法性校验

        规则:
            - key/base_url/agents/judge 要么全不配
            - 要么 4 项一起配齐 当作首次空库种子配置
            - judge.agent 必须出现在 agents 段中
        """
        seed_flags = [
            self.key is not None,
            self.base_url is not None,
            self.agents is not None,
            self.judge is not None,
        ]
        if any(seed_flags) and not all(seed_flags):
            raise ValueError(
                "key/base_url/agents/judge 如需保留种子配置 必须 4 项同时提供"
            )
        if self.judge is None or self.agents is None:
            return self
        if self.judge.agent not in self.agents:
            raise ValueError(
                f"judge.agent={self.judge.agent!r} 不在 agents 段中 候选: {sorted(self.agents.keys())}"
            )
        return self


def _resolve_config_path(path: str | Path | None) -> Path:
    """解析配置文件路径 入参优先 然后环境变量 最后默认相对路径"""
    if path is not None:
        return Path(path).expanduser().resolve()

    env_path = os.environ.get("MULTICHAT_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()

    # 默认指向项目根的 config.yaml 即 backend 目录的上一级
    backend_root = Path(__file__).resolve().parents[3]
    return (backend_root / "config.yaml").resolve()


def _mask_key(value: str) -> str:
    """打印 LLM key 时只暴露末 4 位 避免泄漏"""
    if not value:
        return "<empty>"
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def load_settings(path: str | Path | None = None) -> Settings:
    """从 yaml 加载配置并转成 Settings 实例

    缺字段直接抛 pydantic ValidationError 早暴露 避免运行时再炸
    """
    config_path = _resolve_config_path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"找不到配置文件 {config_path}")

    with config_path.open("r", encoding="utf-8") as fp:
        raw: Any = yaml.safe_load(fp)

    if not isinstance(raw, dict):
        raise ValueError(f"配置文件根节点必须是 mapping 实际类型 {type(raw).__name__}")

    settings = Settings.model_validate(raw)

    _logger.info(
        "配置加载完成",
        config_path=str(config_path),
        mongo_uri=settings.mongo.uri,
        server_host=settings.server.host,
        server_port=settings.server.port,
        llm_key_tail=_mask_key(settings.key or ""),
        agents=sorted((settings.agents or {}).keys()),
        judge_agent=(settings.judge.agent if settings.judge else None),
    )
    return settings

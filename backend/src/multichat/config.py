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
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field, model_validator
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

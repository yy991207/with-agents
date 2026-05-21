"""配置加载模块 基于 pydantic-settings + PyYAML

字段对齐项目根 config.example.yaml 主要包含:
    - key/base_url: LLM 服务凭据与地址
    - agents: 4 个参与者 LLM 的配置 名称 模型 风格等
    - judge: 评判/编排相关配置
    - mongo: MongoDB 连接信息
    - runtime: 运行时调优参数 并发 超时 重试 等
    - server: HTTP 服务监听端口与跨域设置

当前 M1 骨架仅给出字段占位 真实加载逻辑在 M2 阶段补齐
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMKeyConfig(BaseModel):
    """LLM 调用所需的 key 与 base_url 占位"""

    api_key: str = ""
    base_url: str = ""


class AgentConfig(BaseModel):
    """单个参与者 LLM 的配置"""

    name: str = ""
    model: str = ""
    style: str = ""
    base_url: str = ""
    api_key: str = ""


class JudgeConfig(BaseModel):
    """评判/编排相关配置"""

    model: str = ""
    base_url: str = ""
    api_key: str = ""


class MongoConfig(BaseModel):
    """MongoDB 连接配置"""

    uri: str = "mongodb://localhost:27017"
    database: str = "multichat"


class RuntimeConfig(BaseModel):
    """运行时调优参数 并发 超时 重试 等"""

    think_timeout_seconds: int = 30
    reply_timeout_seconds: int = 300
    max_concurrent_tasks: int = 32


class ServerConfig(BaseModel):
    """HTTP 服务监听端口与跨域设置"""

    host: str = "0.0.0.0"
    port: int = 8002
    cors_origins: list[str] = Field(default_factory=list)


class AppSettings(BaseSettings):
    """整体应用配置 后续从 config.yaml 注入"""

    model_config = SettingsConfigDict(
        env_prefix="MULTICHAT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    key: LLMKeyConfig = Field(default_factory=LLMKeyConfig)
    base_url: str = ""
    agents: list[AgentConfig] = Field(default_factory=list)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """从 yaml 文件加载配置 当前为骨架 真实解析逻辑后续补齐

    参数:
        config_path: 配置文件路径 缺省时按项目约定搜索 config.yaml
    """

    raise NotImplementedError("M2 实施 真实 yaml 加载与字段映射在后续模块补齐")


def settings_dict_for_debug() -> dict[str, Any]:
    """调试辅助 占位"""

    raise NotImplementedError("M2 实施")

"""核心 Pydantic 数据模型

包含 think-then-choose 流程中的关键实体:
    - TaskState: 任务状态机枚举
    - ThinkResult: 单个 LLM 在 think 阶段产出的发言理由
    - Round: 一轮交互的完整上下文 含若干 think 结果与最终被选中 LLM 的回复
    - Session: 一次会话 包含多轮 Round 与会话级元数据
    - SessionMeta: 会话列表展示用的轻量元信息
    - AgentRecord: agents collection 中的一条记录 完整数字员工配置
        裁判选谁存放在独立的 settings collection judge_pointer 文档里 与本模型无关
    - ModelCatalogEntry: agent.available_models 列表中的一项 用户可在 agent 配置中维护
    - McpServerConfig: MCP 服务器配置 支持 stdio / sse / streamable_http 三种传输
        持久化在 mcp_servers 集合中 前后端共享配置结构
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    """统一带时区的当前时间 避免 naive datetime 在 mongo 落库时跑偏"""
    return datetime.now(timezone.utc)


# 旧 TaskState 字面量到 spec 新值的映射 仅用于 Round 读取阶段兼容历史数据
# 已落库的字面量保持不动 不主动回写 避免触发全文档覆盖丢失其它字段
_LEGACY_STATE_MAP: dict[str, str] = {
    "created": "pending",
    "waiting_decision": "think_done",
    "failed": "cancelled",
}


class TaskState(str, Enum):
    """think-then-choose 状态机

    枚举值与 spec §4 严格对齐 旧值通过 Round.state 的 field_validator 兼容映射:
        - created          -> pending
        - waiting_decision -> think_done
        - failed           -> cancelled
    实际写入 mongo 时一律使用下面 7 个 spec 值
    """

    PENDING = "pending"
    THINKING = "thinking"
    THINK_DONE = "think_done"
    DECIDED = "decided"
    REPLYING = "replying"
    DONE = "done"
    CANCELLED = "cancelled"


class ThinkResult(BaseModel):
    """单个 LLM 在 think 阶段产出的发言理由"""

    agent_name: str
    reason: str = ""
    latency_ms: int = 0
    error: str | None = None


class Round(BaseModel):
    """一轮交互上下文 一次提问触发的完整 think + reply 周期

    task_id 是 round 全局唯一 id 即对外暴露给前端的 SSE 路径参数

    字段分两套:
        - 历史字段: think_results / chosen_agent / reply_content 由 storage.create_round
          默认写入 与 M0/M1 兼容
        - spec 字段: thinks / decision / reply / think_history 由 task_manager 在运行时
          通过 update_round_field 增量写入 历史回放与 SSE snapshot 都基于这套
    """

    task_id: str
    session_id: str
    round_index: int = 0
    question: str
    user_mention: str | None = None
    # 历史字段 早期持久化结构 兼容已有数据
    think_results: list[ThinkResult] = Field(default_factory=list)
    chosen_agent: str | None = None
    reply_content: str = ""
    state: TaskState = TaskState.PENDING
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # spec 字段 task_manager 运行时使用 默认空 兼容旧数据
    # thinks 形如 {"GLM": {"state":"done","content":"..."}, ...}
    thinks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # decision 形如 {"choice":"GLM","reason":"user_pick","decided_at":"..."}
    decision: dict[str, Any] | None = None
    # reply 形如 {"agent":"GLM","state":"streaming|done|failed","content":"..."}
    reply: dict[str, Any] | None = None
    # think_history 用于 regenerate 之后保留上一轮 think 结果
    think_history: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("state", mode="before")
    @classmethod
    def _migrate_legacy_state(cls, v: Any) -> Any:
        """读取阶段把历史 state 字面量映射到新枚举 防止 ValidationError"""
        if isinstance(v, str) and v in _LEGACY_STATE_MAP:
            return _LEGACY_STATE_MAP[v]
        return v

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _ensure_utc_tz(cls, v: Any) -> Any:
        """mongo BSON datetime 取出来是 naive  统一打上 UTC tz_info
        否则 model_dump(mode='json') 输出 '2026-05-25T08:10:00' 不带时区
        前端 new Date 会当本地时间解析  导致用户气泡少 8 小时"""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class Session(BaseModel):
    """一次会话 含若干轮 Round"""

    session_id: str
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SessionMeta(BaseModel):
    """会话列表展示用 轻量元信息 不带 rounds 详细内容"""

    session_id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime


class ModelCatalogEntry(BaseModel):
    """agent.available_models 中一项可选模型的元信息

    label 用于前端下拉显示 缺省同 model_id
    """

    model_id: str
    label: str = ""


class AgentRecord(BaseModel):
    """agents 集合中的一条记录 一个独立的"数字员工"完整配置

    name 是不可变的内部 ID 由后端生成 形如 agent_<8位hex>
        round.thinks / decision.choice / SSE event 中的引用都用它
        改 display_name 不会破坏历史 round 的引用关系
    display_name 用户可改 UI 显示用 默认初始值 = name
    其它字段都可改 改了立即触发 deep_agent 实例热替换:
        - provider_type  当前固定 openai_compatible 留作日后扩展
        - base_url       LLM 服务地址
        - api_key        LLM 凭据 接口返回前端时由路由层 mask
        - model          当前选用的 model_id
        - available_models  该 agent 维护的可选模型池 前端下拉用
        - prompt         该 agent 的系统提示词
    version 每次 upsert 自增 1 用于前端比对是否本地缓存过期
    """

    name: str
    display_name: str = ""
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    api_key: str
    model: str
    available_models: list[ModelCatalogEntry] = Field(default_factory=list)
    prompt: str
    version: int = 1
    updated_at: datetime = Field(default_factory=_utcnow)
    # 头像 data URL 形如 data:image/png;base64,xxx 没设置时为 None
    # 直接 base64 内联存进 agent doc 不走对象存储 体积上限由路由层校验 ≤2MB
    # 头像变更不触发 version 自增也不进 agent_history  它是展示数据不是配置
    avatar_data_url: str | None = None


# ============================================================ MCP 服务器配置
class McpServerConfig(BaseModel):
    """单个 MCP 服务器的完整配置 持久化在 mcp_servers 集合中

    传输方式 transport 决定必填字段:
        - stdio: 必须填 command + args
        - sse / streamable_http: 必须填 url

    always_allow 是前端可配置的"无需审批直接允许"工具列表
    disabled 控制该 server 是否参与工具加载
    """

    name: str
    """服务器唯一标识 用户自定义 如 playwright / tencentcloud-sdk-mcp"""

    transport: Literal["stdio", "sse", "streamable_http"] = "stdio"
    """传输方式: 本地进程启动 / SSE 长连接 / Streamable HTTP"""

    command: str | None = None
    """stdio 模式: 可执行文件路径 如 npx / uvx / python"""

    args: list[str] = Field(default_factory=list)
    """stdio 模式: 命令行参数 如 ["-y", "@modelcontextprotocol/server-memory"]"""

    env: dict[str, str] = Field(default_factory=dict)
    """环境变量字典 所有传输模式均适用"""

    url: str | None = None
    """sse / streamable_http 模式: 服务器 URL"""

    headers: dict[str, str] = Field(default_factory=dict)
    """sse / streamable_http 模式: 请求头"""

    always_allow: list[str] = Field(default_factory=list)
    """无需审批直接允许的工具名称列表"""

    disabled: bool = False
    """是否禁用该 MCP 服务器 禁用后不参与工具加载"""

    updated_at: datetime = Field(default_factory=_utcnow)


# ============================================================ Skills 配置
class SkillConfig(BaseModel):
    """单个 Skill 的完整配置 持久化在 settings 集合 skills_config 文档中

    Skill 是"可复用提示词片段" 每一个 skill 对应一个 SKILL.md 文件内容
    前端编辑 JSON 配置完成 Skills 的安装/配置/启停

    设计参考 Roo Code 的 SKILL.md 结构:
        - name: skill 名称 对应 SKILL.md 中的 name frontmatter
        - description: skill 简介 对应 SKILL.md 中的 description frontmatter
        - content: SKILL.md 完整正文 包含所有指令与示例
        - enabled: 控制是否注入到 agent 的 system prompt

    被注入的 skill 内容会追加到 reply agent 的 system_prompt 尾部
    让 agent 在执行任务时遵守 skill 定义的流程与规范
    """

    name: str
    """skill 唯一标识 如 brainstorming / systematic-debugging"""

    description: str = ""
    """skill 的一句话描述 前端列表展示用"""

    content: str
    """SKILL.md 完整正文 包含 frontmatter 后的所有 Markdown 指令"""

    enabled: bool = True
    """false 表示禁用 该 skill 内容不注入 system prompt"""

    updated_at: datetime = Field(default_factory=_utcnow)

"""核心 Pydantic 数据模型

包含多 agent 并发回答流程中的关键实体:
    - TaskState: 任务状态机枚举  简化为 4 态 PENDING / REPLYING / DONE / CANCELLED
    - Round: 一轮交互的完整上下文 含本轮发起的 agents 列表与每个 agent 的独立 reply
        多 agent 模式下 N 个 agent 并发跑回答  用户从中选一个作为正式回答(selected_reply_agent)
        单 agent 模式下 agents 仅 1 项  selected_reply_agent 在 reply 完成时由后端自动写入
    - Session: 一次会话 包含多轮 Round 与会话级元数据
    - SessionMeta: 会话列表展示用的轻量元信息
    - AgentRecord: agents collection 中的一条记录 完整数字员工配置
    - ModelCatalogEntry: agent.available_models 列表中的一项 用户可在 agent 配置中维护
    - McpServerConfig: MCP 服务器配置 支持 stdio / sse / streamable_http 三种传输
        持久化在 mcp_servers 集合中 前后端共享配置结构
    - SkillConfig: 单个 Skill 配置  持久化在 settings 集合 skills_config 文档中
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _utcnow() -> datetime:
    """统一带时区的当前时间 避免 naive datetime 在 mongo 落库时跑偏"""
    return datetime.now(timezone.utc)


# 旧 TaskState 字面量到新 4 态的映射 仅用于 Round 读取阶段兼容历史数据
# 老 round 在 mongo 中可能存的是 thinking/think_done/decided/replying 这些中间态
# 历史已 done 的轮次保持 done  历史中间态归一到 cancelled  防止前端误显示"进行中"
# 已落库的字面量保持不动 不主动回写 避免触发全文档覆盖丢失其它字段
_LEGACY_STATE_MAP: dict[str, str] = {
    # M0/M1 时期的旧值
    "created": "pending",
    "waiting_decision": "cancelled",  # 旧 think 阶段中间态  历史轮次直接当作弃用
    "failed": "cancelled",
    # think 流程下线后的中间态  历史轮次跑到一半留下来的全部归 cancelled
    "thinking": "cancelled",
    "think_done": "cancelled",
    "decided": "cancelled",
    # replying 历史里通常已有 reply.content  但状态可能没刷成 done
    # 这里保守归 cancelled  让 _build_history 通过 selected_reply_agent + replies[x].state==done 判定
    "replying": "cancelled",
}


class TaskState(str, Enum):
    """多 agent 并发回答状态机  4 态

    PENDING    刚创建 还没 fan-out 到各 agent
    REPLYING   至少一个 agent 还在 streaming
    DONE       所有 agent reply 都终止(done/failed/cancelled)  等用户选答
    CANCELLED  整轮被全局取消

    不再区分"等用户决策"状态  改由 selected_reply_agent 字段判定
        DONE 且 selected_reply_agent is None  表示等用户选答
        DONE 且 selected_reply_agent 非空  表示用户已选 进入下一轮可发提问
    """

    PENDING = "pending"
    REPLYING = "replying"
    DONE = "done"
    CANCELLED = "cancelled"


class Round(BaseModel):
    """一轮交互上下文 一次提问触发的多 agent 并发回答周期

    task_id 是 round 全局唯一 id 即对外暴露给前端的 SSE 路径参数

    本轮发起字段:
        - agents: 本轮选中发起的 agent name 列表  长度 1~4
        - input_mode: 'single' | 'multi'  单 agent 时 agents 长度=1 自动选中
        - thinking_enabled: 大脑开关  对所有 agents 统一生效

    回答状态字段:
        - replies: dict 形如 {"agent_a": {"state":"streaming|done|failed|cancelled",
                                          "content":"...","segments":[...],
                                          "started_at":"...","finished_at":"...",
                                          "error":"...optional..."}}
        - selected_reply_agent: 用户从 replies 中选定的 agent name  为 None 表示未选答
            单 agent 模式 reply 完成时后端自动写入
            多 agent 模式由前端 /select_reply 路由触发写入
            未选答时下一轮 /ask 返回 409  阻止用户继续发提问
    """

    task_id: str
    session_id: str
    round_index: int = 0
    question: str
    user_mention: str | None = None
    # 本轮是否启用 thinking 模式  前端大脑开关传过来  后端据此给 ChatOpenAI 注入
    # extra_body={"thinking":{"type":"enabled"}}  让支持的模型走深度思考分支
    # 不影响 reasoning_content 提取逻辑  那个始终开 兼容 deepseek-reasoner / glm 等"自带 reasoning"的模型
    thinking_enabled: bool = False
    # 本轮发起的 agent name 列表  长度 1~4
    # 单 agent 模式 长度=1  多 agent 模式 长度 2~4
    agents: list[str] = Field(default_factory=list)
    # 输入模式  single 单 agent  multi 多 agent
    input_mode: Literal["single", "multi"] = "single"
    state: TaskState = TaskState.PENDING
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # 每个 agent 的独立 reply  key=agent.name  并发跑彼此独立
    # 形如 {"glm": {"state":"streaming","content":"...","segments":[...]}}
    replies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # 用户选中的 agent name  None 表示未选  单 agent 模式 reply 完成时自动赋值
    selected_reply_agent: str | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _migrate_legacy_state(cls, v: Any) -> Any:
        """读取阶段把历史 state 字面量映射到新 4 态 防止 ValidationError"""
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

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_round(cls, data: Any) -> Any:
        """读取阶段把老 round 文档(单 reply 模型)迁移到新 replies 模型

        老结构:
            {
                "reply": {"agent":"glm","state":"done","content":"...","segments":[...]},
                "chosen_agent": "glm",
                "thinks": {...},
                "decision": {...},
                ...
            }
        新结构:
            {
                "agents": ["glm"],
                "input_mode": "single",
                "replies": {"glm": {"state":"done","content":"...","segments":[...]}},
                "selected_reply_agent": "glm",
            }

        只在数据缺新字段且有老字段时迁移  对新数据透传
        thinks/decision/think_history/think_results/chosen_agent/reply_content/reply
        这些老字段 pydantic v2 默认 extra='ignore' 自动丢弃  不需要在这里处理
        """
        if not isinstance(data, dict):
            return data

        # 已经是新结构  直接透传
        has_replies = bool(data.get("replies"))
        has_agents = bool(data.get("agents"))
        if has_replies and has_agents:
            return data

        legacy_reply = data.get("reply")
        # 历史 round 没有 reply 字段时无可迁移  保持空 replies + agents
        if not isinstance(legacy_reply, dict):
            return data

        legacy_agent = (
            legacy_reply.get("agent")
            or data.get("chosen_agent")
            or ""
        )
        if not isinstance(legacy_agent, str) or not legacy_agent:
            return data

        # 把老 reply 整个塞到 replies[legacy_agent]  剥掉 agent 字段(replies key 已经表达)
        old_reply_inner: dict[str, Any] = {
            k: v for k, v in legacy_reply.items() if k != "agent"
        }
        if not has_replies:
            data["replies"] = {legacy_agent: old_reply_inner}
        if not has_agents:
            data["agents"] = [legacy_agent]
        if not data.get("input_mode"):
            data["input_mode"] = "single"
        # 老数据如果 reply.state 是 done  默认就把 selected_reply_agent 写为这个 agent
        # 让历史已完成的轮次刷新页面后能直接进入下一轮提问 不会被 selectedReplyAgent 校验拦截
        if data.get("selected_reply_agent") is None:
            if old_reply_inner.get("state") == "done":
                data["selected_reply_agent"] = legacy_agent
        return data


class Session(BaseModel):
    """一次会话 含若干轮 Round

    摘要相关字段:
        - summary: 已压缩内容 LLM 生成 单条覆盖更新 不存历史摘要
        - summary_until_round: 摘要覆盖到的 round_index 拼 history 时只追加该值之后的 round
        - summary_updated_at: 最近一次摘要时间 None 表示尚未做过摘要

    上下文用量字段:
        - context_usage: 最近一次 SSE context.usage 事件 payload 落库快照
          字段与 token_counter.usage_payload 对齐 None 表示未上报过
          供前端刷新 / 切会话时从 history 接口直接恢复进度条状态
    """

    session_id: str
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    summary: str = ""
    summary_until_round: int = 0
    summary_updated_at: datetime | None = None
    context_usage: dict[str, Any] | None = None
    # 分支会话树字段:
    #   parent_session_id  指向父会话  None 表示根会话
    #   branch_from_task_id 指向父会话里触发分支的那一轮
    #   branch_from_role    分支锚点是 user 还是 assistant
    #   branch_from_agent   assistant 分支时记录选中的 agent  user 分支为空
    parent_session_id: str | None = None
    branch_from_task_id: str | None = None
    branch_from_role: Literal["user", "assistant"] | None = None
    branch_from_agent: str | None = None
    draft_message: str | None = None

    @field_validator("summary_updated_at", mode="before")
    @classmethod
    def _ensure_utc_tz_optional(cls, v: Any) -> Any:
        """与 Round 同款 mongo 取出来 naive 给打 UTC tz_info"""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class SessionMeta(BaseModel):
    """会话列表展示用 轻量元信息 不带 rounds 详细内容"""

    session_id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime
    parent_session_id: str | None = None
    branch_from_task_id: str | None = None
    branch_from_role: Literal["user", "assistant"] | None = None
    branch_from_agent: str | None = None
    draft_message: str | None = None


class ModelCatalogEntry(BaseModel):
    """agent.available_models 中一项可选模型的元信息

    label 用于前端下拉显示 缺省同 model_id
    max_input_tokens 用户在 agent 表单里手动维护 摘要触发阈值 = 此值 × 80%
        必填字段 历史数据若缺失走路由层兜底校验
    """

    model_id: str
    label: str = ""
    max_input_tokens: int = Field(..., gt=0, description="模型最大输入 token 窗口")


class AgentRecord(BaseModel):
    """agents 集合中的一条记录 一个独立的"数字员工"完整配置

    name 是不可变的内部 ID 由后端生成 形如 agent_<8位hex>
        round.replies key / SSE event 中的引用都用它
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

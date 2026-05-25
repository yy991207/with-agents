// 全局类型定义:任务状态、SSE 事件、会话与轮次视图模型
// 数字员工模型重构后:agent 数量与名称完全由后端返回 不再固定四个

// 单次任务的状态机
export type TaskState =
  | 'PENDING'
  | 'THINKING'
  | 'THINK_DONE'
  | 'DECIDED'
  | 'REPLYING'
  | 'DONE'
  | 'CANCELLED';

// agent 名称:不再写死成 union 由后端动态返回 各 agent 的内部 ID
export type AgentName = string;

// SSE 事件统一外壳:具体字段由 type 决定 这里用宽松对象兜底
export interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}

// 单个 think 卡片的内部状态
export type ThinkState = 'pending' | 'done' | 'failed' | 'cancelled' | 'skipped';

// 单个 think 卡片视图模型
export interface ThinkView {
  agent: AgentName;
  state: ThinkState;
  content?: string;
  error?: string;
}

// 决策结果:某个 agent / 让后端帮选 / 重新 think
export interface DecisionView {
  choice: AgentName | 'auto' | 'regenerate';
  reason: string;
}

// 回复时间线中的一段: 文本或工具调用 按时间顺序排列
export interface ReplySegment {
  type: 'text' | 'tool_call' | 'tool_result';
  content?: string;       // type=text 时的文本内容
  tool?: string;          // type=tool_call/tool_result 时的工具名
  input?: string;         // type=tool_call 时的入参
  result?: string;        // type=tool_result 时的返回
}

// reply 段中的工具调用事件: 保留兼容旧数据
export interface ToolCallEvent {
  tool: string;
  input?: string;
  result?: string;
}

// reply 视图状态
export type ReplyState = 'pending' | 'streaming' | 'done' | 'failed' | 'cancelled';

// 单次回答气泡视图
export interface ReplyView {
  agent: AgentName;
  state: ReplyState;
  content: string;
  toolCalls: ToolCallEvent[];
  segments: ReplySegment[];   // 按时间顺序排列的文本+工具调用
  error?: string;
  // reply 写完时的 ISO8601  用于在 reply 头部显示完成时间  没值就不显示
  finishedAt?: string;
}

// 一轮完整对话(用户消息 + N 个 think + 决策 + 回答)
// thinks 改成普通 Record key 由后端返回的 agent.name 决定
export interface RoundView {
  taskId: string;
  state: TaskState;
  userMessage: string;
  thinks: Record<string, ThinkView>;
  decision?: DecisionView;
  reply?: ReplyView;
  // think_done 时后端会推可用 agent 列表(失败/取消的会被剔除)
  availableAgents?: AgentName[];
  // judge 模式下后端建议或最终选择的 agent
  judgePick?: AgentName;
  // 任务被取消时 前端展示一个简短原因
  cancelReason?: string;
  // 用户气泡显示用的 ISO8601  来自 round.created_at  POST /ask 创建时落库
  createdAt?: string;
}

// 会话元信息(列表用)
export interface SessionMeta {
  sessionId: string;
  title: string;
  updatedAt: string;
}

// /history 返回结构
export interface HistoryResponse {
  session: SessionMeta;
  rounds: RoundView[];
}

// SSE 连接状态
export type SSEStatus = 'idle' | 'open' | 'closed' | 'reconnecting';

// agent 子模型视图(provider 候选模型 也用同一结构)
export interface ModelView {
  model_id: string;
  label: string;
}

// POST /api/models/discover 动态拉取 OpenAI 兼容 provider 的模型列表
export interface DiscoverModelsRequest {
  base_url: string;
  api_key: string;
  provider_type?: string;
}

export interface DiscoverModelsResponse {
  models: ModelView[];
}

// 单个 agent 完整配置视图
// 注意:GET 时 api_key 是 mask 形式 "sk-...xxxx" PUT 时不传或空字符串保留旧值
export interface AgentView {
  name: string;                // 内部 ID 不可变
  display_name: string;        // 用户可改的展示名
  provider_type: string;       // 当前固定 "openai_compatible"
  base_url: string;
  api_key: string;             // mask
  model: string;
  available_models: ModelView[];
  prompt: string;
  version: number;
  updated_at: string;
  // 头像 data URL 形如 data:image/png;base64,xxx 没设置时 null
  // 直出给 <img src=> 用 后端 base64 内联存进 mongo agent doc
  avatar_data_url: string | null;
}

// /api/agents 列表响应
export interface AgentsListResponse {
  agents: AgentView[];
  judge_target: string;        // 当前 judge 指向哪个 agent.name
}

// POST /api/agents 创建一个新 agent
// 传 copy_key_from 时复用已有 agent 的 key，api_key 可以省略
export interface CreateAgentRequest {
  display_name: string;        // 1-64 字符
  base_url: string;            // ≥8 字符
  api_key?: string;            // ≥4 字符；传 copy_key_from 时可省略
  model: string;               // ≥1 字符
  prompt: string;              // ≥5 字符
  available_models?: ModelView[];
  provider_type?: string;      // 默认 openai_compatible
  copy_key_from?: string;      // 从已有 agent 复制 key，优先级高于 api_key
}

// PUT /api/agents/{name} 更新 agent
// 不传 api_key 表示保留旧 传空字符串也保留旧 仅当 dirty 时才会被写入
export interface UpdateAgentRequest {
  display_name?: string;
  base_url?: string;
  api_key?: string;
  model?: string;
  available_models?: ModelView[];
  prompt?: string;
  provider_type?: string;
  expected_version?: number;
}

// PUT /api/judge 请求体
export interface UpdateJudgeRequest {
  target: string;
}

// 单个 agent 的本地编辑稿
export interface AgentEditDraft {
  name: string;                // 不可改 内部 ID
  displayName: string;
  providerType: string;
  baseUrl: string;
  apiKey: string;              // 仅当用户主动改才有意义 否则提交时不写入 body
  apiKeyDirty: boolean;        // 用户是否主动改过 api_key
  apiKeyMask: string;          // 服务端返回的 mask 形式 仅用于 UI placeholder 提示
  model: string;
  availableModels: ModelView[];
  prompt: string;
  version: number;             // 服务端版本 用于乐观锁
  dirty: boolean;              // 是否有未保存改动
  // 头像 data URL  null = 未设置  上传 / 删除走独立接口  不参与 dirty / save
  avatarDataUrl: string | null;
}

// Settings 抽屉的子状态
export interface SettingsState {
  open: boolean;
  loading: boolean;
  saving: boolean;
  drafts: Record<string, AgentEditDraft>;   // key = agent.name (内部 ID)
  judgeTarget: string | null;
  activeAgentName: string | null;            // 当前选中 tab 对应的 agent.name
}

// 工作台页面壳状态：只控制当前看哪个页与侧栏展开，不参与业务任务流
export type WorkbenchView =
  | 'home'
  | 'chat'
  | 'tasks'
  | 'page'
  | 'image'
  | 'community'
  | 'resource';

export interface WorkbenchState {
  activeView: WorkbenchView;
  sidebarCollapsed: boolean;
  recentExpanded: boolean;
  agentsExpanded: boolean;
  recommendPage: number;
}

// 全局 Chat 状态
export interface ChatState {
  sessionId: string | null;
  sessions: SessionMeta[];
  rounds: RoundView[];
  activeTaskId: string | null;
  taskState: TaskState;
  sseStatus: SSEStatus;
  settings: SettingsState;
  workbench: WorkbenchState;
}

// reducer action 列表
export type ChatAction =
  | { type: 'session.set'; sessionId: string | null }
  | { type: 'session.switch'; sessionId: string | null }
  | { type: 'session.deleted'; sessionId: string }
  | { type: 'sessions.set'; sessions: SessionMeta[] }
  | { type: 'rounds.set'; rounds: RoundView[] }
  | { type: 'round.append'; round: RoundView }
  | { type: 'round.update'; taskId: string; patch: Partial<RoundView> }
  | { type: 'task.created'; sessionId: string; taskId: string; userMessage: string; createdAt?: string }
  // 抗刷新重连场景:把 activeTaskId 重新挂回去 准备接收 snapshot 帧
  | { type: 'task.resume'; taskId: string; taskState?: TaskState }
  | { type: 'task.state'; state: TaskState }
  | { type: 'sse.status'; status: SSEStatus }
  | { type: 'sse.event'; taskId?: string; event: SSEEvent }
  | { type: 'history.loaded'; sessionId: string; rounds: RoundView[] }
  // 工作台 UI 壳层相关 action
  | { type: 'ui.view.set'; view: WorkbenchView }
  | { type: 'ui.sidebar.toggle'; collapsed?: boolean }
  | { type: 'ui.section.toggle'; section: 'recent' | 'agents' }
  | { type: 'ui.recommend.rotate' }
  // 配置抽屉相关 action
  | { type: 'settings.open' }
  | { type: 'settings.close' }
  | { type: 'settings.loading.start' }
  | { type: 'settings.loaded'; agents: AgentView[]; judgeTarget: string }
  | { type: 'settings.draft.field'; agentName: string; patch: Partial<AgentEditDraft> }
  | { type: 'settings.saving.start' }
  | { type: 'settings.saved'; agent: AgentView }
  | { type: 'settings.judge.set'; target: string }
  | { type: 'settings.agent.created'; agent: AgentView }
  | { type: 'settings.agent.deleted'; name: string }
  | { type: 'settings.agent.tab.switch'; name: string }
  // 头像上传 / 删除走独立路径  不参与 draft.dirty 也不需要 save 按钮触发
  | { type: 'settings.agent.avatar.set'; agentName: string; avatarDataUrl: string | null }
  | { type: 'settings.error'; message: string };

// 任务忙碌态判定:THINKING / THINK_DONE / DECIDED / REPLYING 视为忙
// 不含 PENDING 不含 DONE / CANCELLED
export function isBusyState(state: TaskState | null | undefined): boolean {
  if (!state) return false;
  return (
    state === 'THINKING' ||
    state === 'THINK_DONE' ||
    state === 'DECIDED' ||
    state === 'REPLYING'
  );
}

// ====== MCP 服务器配置相关类型 ======

// MCP 传输方式
export type McpTransport = 'stdio' | 'sse' | 'streamable_http';

// 单个 MCP 服务器的完整配置视图 (对齐后端 McpServerView)
export interface McpServerView {
  name: string;
  transport: McpTransport;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  always_allow: string[];
  disabled: boolean;
  updated_at: string;
}

// GET /api/mcp/servers 响应
export interface McpServersListResponse {
  servers: McpServerView[];
}

// 本地编辑稿 用于表单双向绑定
export interface McpServerDraft {
  name: string;
  transport: McpTransport;
  command: string;
  argsText: string;       // 编辑用文本 逗号/换行分隔
  envText: string;        // 编辑用文本 KEY=VALUE 每行一个
  url: string;
  headersText: string;    // 编辑用文本 KEY=VALUE 每行一个
  alwaysAllowText: string;// 编辑用文本 逗号/换行分隔
  disabled: boolean;
  dirty: boolean;         // 是否有未保存改动
  isNew: boolean;         // 是否是新创建的（还未保存到后端）
}

// ====== Skills 配置相关类型 ======

// 单个 skill 的配置视图 (对齐后端 SkillItem)
export interface SkillView {
  name: string;
  description: string;
  content: string;
  enabled: boolean;
}

// GET /api/skills/config 响应
export interface SkillsConfigResponse {
  skills: SkillView[];
}

// PUT /api/skills/config 请求
export interface SkillsConfigRequest {
  skills: SkillView[];
}

// 本地编辑稿 一个 skill 的编辑状态
export interface SkillEditDraft {
  name: string;
  description: string;
  content: string;
  enabled: boolean;
  dirty: boolean;
  isNew: boolean;
}

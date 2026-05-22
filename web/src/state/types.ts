// 全局类型定义:任务状态、SSE 事件、会话与轮次视图模型
// 注意:M4 阶段的扩展集中在 RoundView / ReplyView / ChatAction 三块

// 单次任务的状态机
export type TaskState =
  | 'PENDING'
  | 'THINKING'
  | 'THINK_DONE'
  | 'DECIDED'
  | 'REPLYING'
  | 'DONE'
  | 'CANCELLED';

// 受支持的模型名称(后端约定:M1 阶段四个固定 agent)
export type AgentName = 'DeepSeek' | 'GLM' | 'Kimi' | 'Qwen';

// 已知 agent 列表,组件循环渲染时使用
export const KNOWN_AGENTS: AgentName[] = ['DeepSeek', 'GLM', 'Kimi', 'Qwen'];

// SSE 事件统一外壳:具体字段由 type 决定,这里用宽松对象兜底
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

// 决策结果:四选一 / 让后端帮选 / 重新 think
export interface DecisionView {
  choice: AgentName | 'auto' | 'regenerate';
  reason: string;
}

// reply 段中的工具调用事件:tool_call 与 tool_result 配对
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
  error?: string;
}

// 一轮完整对话(用户消息 + 4 个 think + 决策 + 回答)
export interface RoundView {
  taskId: string;
  state: TaskState;
  userMessage: string;
  thinks: Record<AgentName, ThinkView>;
  decision?: DecisionView;
  reply?: ReplyView;
  // think_done 时后端会推可用 agent 列表(失败/取消的会被剔除)
  availableAgents?: AgentName[];
  // judge 模式下后端建议或最终选择的 agent
  judgePick?: AgentName;
  // 任务被取消时,前端展示一个简短原因
  cancelReason?: string;
}

// 会话元信息(列表用)
export interface SessionMeta {
  sessionId: string;
  title: string;
  updatedAt: string;
}

// /history 返回结构:M3 后端约定为 { session, rounds[] }
export interface HistoryResponse {
  session: SessionMeta;
  rounds: RoundView[];
}

// SSE 连接状态
export type SSEStatus = 'idle' | 'open' | 'closed' | 'reconnecting';

// 4 个 agent 的配置视图(M1.D 配置抽屉用)
export interface AgentView {
  name: string; // DeepSeek / GLM / Kimi / Qwen
  model: string; // 如 deepseek-v4-pro
  prompt: string; // system_prompt
  version: number; // 改一次 +1
  updated_at: string; // ISO 字符串
}

// /api/agents 列表响应
export interface AgentsListResponse {
  agents: AgentView[];
  judge_target: string; // 当前 judge 指向哪个 agent
}

// PUT /api/agents/{name} 请求体:model 与 prompt 至少传一项
export interface UpdateAgentRequest {
  model?: string;
  prompt?: string;
  expected_version?: number; // 可选乐观锁
}

// PUT /api/agents/{name} 响应
export interface UpdateAgentResponse {
  name: string;
  version: number;
  reloaded: boolean;
}

// PUT /api/judge 请求体
export interface UpdateJudgeRequest {
  target: string;
}

// 单个 agent 的本地编辑稿
export interface AgentEditDraft {
  name: string;
  model: string;
  prompt: string;
  version: number; // 服务端版本 用于乐观锁
  dirty: boolean; // 用户有未保存改动
}

// Settings 抽屉的子状态
export interface SettingsState {
  open: boolean; // SettingsDrawer 是否打开
  loading: boolean; // 拉取中
  saving: boolean; // 保存中
  drafts: Record<string, AgentEditDraft>; // 4 个 agent 的编辑稿 按 name 索引
  judgeTarget: string | null;
}

// 全局 Chat 状态
export interface ChatState {
  sessionId: string | null;
  sessions: SessionMeta[];
  rounds: RoundView[];
  activeTaskId: string | null;
  taskState: TaskState;
  sseStatus: SSEStatus;
  // M1.D:配置抽屉
  settings: SettingsState;
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
  | { type: 'task.created'; sessionId: string; taskId: string; userMessage: string }
  // 抗刷新重连场景:把 activeTaskId 重新挂回去,准备接收 snapshot 帧
  | { type: 'task.resume'; taskId: string; taskState?: TaskState }
  | { type: 'task.state'; state: TaskState }
  | { type: 'sse.status'; status: SSEStatus }
  | { type: 'sse.event'; event: SSEEvent }
  | { type: 'history.loaded'; sessionId: string; rounds: RoundView[] }
  // 配置抽屉相关 action
  | { type: 'settings.open' }
  | { type: 'settings.close' }
  | { type: 'settings.loading.start' }
  | { type: 'settings.loaded'; agents: AgentView[]; judgeTarget: string }
  | { type: 'settings.draft.update'; name: string; field: 'model' | 'prompt'; value: string }
  | { type: 'settings.saving.start' }
  | { type: 'settings.saved'; name: string; version: number }
  | { type: 'settings.judge.set'; target: string }
  | { type: 'settings.error'; message: string };

// 任务忙碌态判定:THINKING / THINK_DONE / DECIDED / REPLYING 视为忙
// 不含 PENDING(刚创建瞬间,马上会进入 THINKING),也不含 DONE / CANCELLED
// UI 锁(发送按钮、输入框)统一用这个 helper,保持一处定义
export function isBusyState(state: TaskState | null | undefined): boolean {
  if (!state) return false;
  return (
    state === 'THINKING' ||
    state === 'THINK_DONE' ||
    state === 'DECIDED' ||
    state === 'REPLYING'
  );
}

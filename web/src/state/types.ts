// 全局类型定义:任务状态、SSE 事件、会话与轮次视图模型

// 单次任务的状态机
export type TaskState =
  | 'PENDING'
  | 'THINKING'
  | 'THINK_DONE'
  | 'DECIDED'
  | 'REPLYING'
  | 'DONE'
  | 'CANCELLED';

// 受支持的模型名称(后端约定)
export type AgentName = 'DeepSeek' | 'GLM' | 'Kimi' | 'Qwen';

// SSE 事件统一外壳:具体字段由 type 决定,这里用宽松对象兜底
export interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}

// 单个 think 卡片视图模型
export interface ThinkView {
  agent: AgentName;
  state: 'pending' | 'done' | 'failed' | 'cancelled' | 'skipped';
  content?: string;
  error?: string;
}

// 决策结果:四选一 / 让后端帮选 / 重新 think
export interface DecisionView {
  choice: AgentName | 'auto' | 'regenerate';
  reason: string;
}

// 单次回答气泡视图
export interface ReplyView {
  agent: AgentName;
  content: string;
  state: 'pending' | 'streaming' | 'done' | 'failed' | 'cancelled';
}

// 一轮完整对话(用户消息 + 4 个 think + 决策 + 回答)
export interface RoundView {
  taskId: string;
  state: TaskState;
  userMessage: string;
  thinks: Record<AgentName, ThinkView>;
  decision?: DecisionView;
  reply?: ReplyView;
}

// 会话元信息(列表用)
export interface SessionMeta {
  sessionId: string;
  title: string;
  updatedAt: string;
}

// SSE 连接状态
export type SSEStatus = 'idle' | 'open' | 'closed' | 'reconnecting';

// 全局 Chat 状态
export interface ChatState {
  sessionId: string | null;
  sessions: SessionMeta[];
  rounds: RoundView[];
  activeTaskId: string | null;
  taskState: TaskState;
  sseStatus: SSEStatus;
}

// reducer action 列表
export type ChatAction =
  | { type: 'session.set'; sessionId: string | null }
  | { type: 'sessions.set'; sessions: SessionMeta[] }
  | { type: 'rounds.set'; rounds: RoundView[] }
  | { type: 'round.append'; round: RoundView }
  | { type: 'round.update'; taskId: string; patch: Partial<RoundView> }
  | { type: 'task.created'; taskId: string; userMessage: string }
  | { type: 'task.state'; state: TaskState }
  | { type: 'sse.status'; status: SSEStatus }
  | { type: 'sse.event'; event: SSEEvent };

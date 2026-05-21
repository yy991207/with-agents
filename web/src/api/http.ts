// HTTP 客户端封装:统一前缀、统一 JSON、统一异常
// 字段命名严格对齐后端 M3 契约(snake_case),保持类型在前端层面 camelCase
import type {
  AgentName,
  AgentsListResponse,
  HistoryResponse,
  SessionMeta,
  UpdateAgentRequest,
  UpdateAgentResponse,
} from '../state/types';

// 后端基址:dev 走 vite proxy,prod 与同源部署即可
const BASE = '';

// 解析后端错误响应:FastAPI 默认 HTTPException 会返回 { detail: ... } 结构
async function extractErrorDetail(resp: Response): Promise<string> {
  // 先尝试按 JSON 解析,失败再退化到纯文本
  try {
    const data = (await resp.clone().json()) as { detail?: unknown };
    if (data && typeof data.detail === 'string') return data.detail;
    if (data && data.detail !== undefined) return JSON.stringify(data.detail);
  } catch {
    // 不是 JSON 就走纯文本
  }
  try {
    const text = await resp.text();
    if (text) return text;
  } catch {
    // 读不到 body 就用 statusText 兜底
  }
  return resp.statusText || '';
}

// 统一 JSON 请求:成功返回解析后的 JSON,失败抛带状态码 + detail 的 Error
async function request<T>(
  path: string,
  init?: { method?: string; body?: unknown; signal?: AbortSignal },
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: init?.method ?? 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init?.signal,
  });
  if (!resp.ok) {
    const detail = await extractErrorDetail(resp);
    throw new Error(`HTTP ${resp.status}: ${detail}`);
  }
  // 后端约定全部返回 JSON
  return (await resp.json()) as T;
}

// 不需要响应体的请求,如 PUT /api/judge / POST /decide / POST /cancel 走 204
async function requestNoContent(
  path: string,
  init?: { method?: string; body?: unknown; signal?: AbortSignal },
): Promise<void> {
  const resp = await fetch(`${BASE}${path}`, {
    method: init?.method ?? 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    signal: init?.signal,
  });
  if (!resp.ok) {
    const detail = await extractErrorDetail(resp);
    throw new Error(`HTTP ${resp.status}: ${detail}`);
  }
}

// /ask 请求体(后端要求 snake_case)
export interface AskPayload {
  session_id?: string;
  user_message: string;
}

export interface AskResponse {
  session_id: string;
  task_id: string;
}

// 发起一次问答任务
export function ask(payload: AskPayload): Promise<AskResponse> {
  return request<AskResponse>('/ask', { method: 'POST', body: payload });
}

// /decide 请求体:用户在 4 个 think 中选一个,或要求 auto / regenerate
export interface DecidePayload {
  task_id: string;
  choice: AgentName | 'auto' | 'regenerate';
}

// /decide 后端返回 204
export function decide(payload: DecidePayload): Promise<void> {
  return requestNoContent('/decide', { method: 'POST', body: payload });
}

// /cancel:取消任务,scope = 'global' | AgentName
export interface CancelPayload {
  task_id: string;
  scope: 'global' | AgentName;
}

export function cancel(payload: CancelPayload): Promise<void> {
  return requestNoContent('/cancel', { method: 'POST', body: payload });
}

// /retry-think:针对某一个 agent 重试 think(M2 暂未实装,后端会返回 501)
export interface RetryThinkPayload {
  task_id: string;
  agent: AgentName;
}

export function retryThink(payload: RetryThinkPayload): Promise<void> {
  return requestNoContent('/retry-think', { method: 'POST', body: payload });
}

// /history/:sessionId:拉取某会话历史,返回 { session, rounds[] }
export function getHistory(sessionId: string): Promise<HistoryResponse> {
  return request<HistoryResponse>(`/history/${encodeURIComponent(sessionId)}`);
}

// /sessions:会话列表
export function listSessions(): Promise<SessionMeta[]> {
  return request<SessionMeta[]>('/sessions');
}

// ====== M1.D 配置抽屉相关 API ======

// GET /api/agents:拉取 4 个 agent 的当前配置以及 judge 指向
export function getAgents(): Promise<AgentsListResponse> {
  return request<AgentsListResponse>('/api/agents');
}

// PUT /api/agents/{name}:更新某个 agent 的 model 或 prompt
export function updateAgent(
  name: string,
  body: UpdateAgentRequest,
): Promise<UpdateAgentResponse> {
  return request<UpdateAgentResponse>(`/api/agents/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body,
  });
}

// PUT /api/judge:切换 judge 指向哪个 agent,后端返回 204 无 body
export function updateJudge(target: string): Promise<void> {
  return requestNoContent('/api/judge', {
    method: 'PUT',
    body: { target },
  });
}

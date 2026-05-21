// HTTP 客户端封装:统一前缀、统一 JSON、统一异常
import type { AgentName, RoundView, SessionMeta } from '../state/types';

// 后端基址:dev 走 vite proxy,prod 与同源部署即可
const BASE = '';

// 统一 JSON 请求
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
    // 简单读取错误体,便于上层做提示
    const text = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}: ${text || resp.statusText}`);
  }
  // 后端约定全部返回 JSON
  return (await resp.json()) as T;
}

// /ask 请求体
export interface AskPayload {
  sessionId: string | null;
  message: string;
}

export interface AskResponse {
  taskId: string;
  sessionId: string;
}

// 发起一次问答任务
export function ask(payload: AskPayload): Promise<AskResponse> {
  return request<AskResponse>('/ask', { method: 'POST', body: payload });
}

// /decide 请求体:用户在 4 个 think 中选一个,或要求 auto / regenerate
export interface DecidePayload {
  taskId: string;
  choice: AgentName | 'auto' | 'regenerate';
  reason?: string;
}

export function decide(payload: DecidePayload): Promise<{ ok: true }> {
  return request<{ ok: true }>('/decide', { method: 'POST', body: payload });
}

// /cancel:取消进行中的任务
export function cancel(taskId: string): Promise<{ ok: true }> {
  return request<{ ok: true }>('/cancel', { method: 'POST', body: { taskId } });
}

// /retry-think:针对某一个 agent 重试 think
export interface RetryThinkPayload {
  taskId: string;
  agent: AgentName;
}

export function retryThink(payload: RetryThinkPayload): Promise<{ ok: true }> {
  return request<{ ok: true }>('/retry-think', { method: 'POST', body: payload });
}

// /history/:sessionId:拉取某会话历史
export function getHistory(sessionId: string): Promise<RoundView[]> {
  return request<RoundView[]>(`/history/${encodeURIComponent(sessionId)}`);
}

// /sessions:会话列表
export function listSessions(): Promise<SessionMeta[]> {
  return request<SessionMeta[]>('/sessions');
}

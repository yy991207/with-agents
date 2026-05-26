// HTTP 客户端封装:统一前缀、统一 JSON、统一异常
// 字段命名严格对齐后端契约(snake_case) 前端层面 camelCase
import type {
  AgentName,
  AgentView,
  AgentsListResponse,
  CreateAgentRequest,
  DiscoverModelsRequest,
  DiscoverModelsResponse,
  HistoryResponse,
  SessionMeta,
  UpdateAgentRequest,
} from '../state/types';
import { convertAgentView } from '../state/converters';

// 后端基址:dev 走 vite proxy prod 同源
const BASE = '';

// 解析后端错误响应:FastAPI 默认 HTTPException 返回 { detail: ... }
async function extractErrorDetail(resp: Response): Promise<string> {
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

// 统一 JSON 请求:成功返回解析后的 JSON 失败抛带状态码 + detail 的 Error
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
  return (await resp.json()) as T;
}

// 不需要响应体的请求 走 204
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
  // 本轮发起的 agent name 列表  长度 1~4
  agents: string[];
  // 单/多 agent 模式  对应输入框切换
  input_mode: 'single' | 'multi';
  // 是否启用深度思考  对应输入框的大脑开关  本轮一次性
  // 后端据此给 ChatOpenAI 注入 extra_body={"thinking":{"type":"enabled"}}
  thinking?: boolean;
  // 编辑已有 user 消息后重发时传入  后端会清掉该轮之后的历史
  replace_task_id?: string;
}

export interface AskResponse {
  session_id: string;
  task_id: string;
  // 后端 round.created_at 透传  ISO8601 字符串  用于用户气泡显示创建时间
  created_at: string;
}

// 发起一次问答任务
export function ask(payload: AskPayload): Promise<AskResponse> {
  return request<AskResponse>('/ask', { method: 'POST', body: payload });
}

// /cancel scope = 'global' | AgentName
export interface CancelPayload {
  task_id: string;
  scope: 'global' | AgentName;
}

export function cancel(payload: CancelPayload): Promise<void> {
  return requestNoContent('/cancel', { method: 'POST', body: payload });
}

// /select_reply 用户选定本轮某个 agent 的回答作为正式回答
export interface SelectReplyPayload {
  task_id: string;
  agent: AgentName;
}

export function selectReply(payload: SelectReplyPayload): Promise<void> {
  return requestNoContent('/select_reply', { method: 'POST', body: payload });
}

// /retry_reply 重答某 agent  其它 agent 不动
export interface RetryReplyPayload {
  task_id: string;
  agent: AgentName;
}

export function retryReply(payload: RetryReplyPayload): Promise<void> {
  return requestNoContent('/retry_reply', { method: 'POST', body: payload });
}

// /history/:sessionId
export function getHistory(sessionId: string): Promise<HistoryResponse> {
  return request<HistoryResponse>(`/history/${encodeURIComponent(sessionId)}`);
}

// /sessions
export function listSessions(): Promise<SessionMeta[]> {
  return request<SessionMeta[]>('/sessions');
}

// DELETE /sessions/{id}
export function deleteSession(sessionId: string): Promise<void> {
  return requestNoContent(`/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
}

export interface BatchDeleteResult {
  deleted: number;
  skipped: number;
  errors: string[];
}

// POST /sessions/batch-delete 批量删除会话
export function batchDeleteSessions(
  sessionIds: string[],
): Promise<BatchDeleteResult> {
  return request<BatchDeleteResult>('/sessions/batch-delete', {
    method: 'POST',
    body: { session_ids: sessionIds },
  });
}

export interface BranchSessionPayload {
  source_task_id: string;
  source_role: 'user' | 'assistant';
  source_agent?: string;
}

export interface BranchSessionResponse {
  session_id: string;
  draft_message?: string | null;
}

export function branchSession(
  sessionId: string,
  payload: BranchSessionPayload,
): Promise<BranchSessionResponse> {
  return request<BranchSessionResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/branch`,
    {
      method: 'POST',
      body: payload,
    },
  );
}

// ====== 会话上下文压缩 ======

// POST /api/sessions/{session_id}/compact 一键压缩响应
// 同步路由  等后端摘要写入并落库后返回  耗时通常 30-90s
//   summary               最新累计摘要文本
//   summary_until_round   摘要覆盖到的轮次序号  含
//   summary_updated_at    摘要写入时间 ISO8601
//   used_tokens_before    压缩前 token 用量
//   used_tokens_after     压缩后 token 用量  通常远小于 before
//   max_input_tokens      当前模型最大输入窗口  用于重组 ContextUsage
//   model_id              当前模型 ID
export interface CompactResponse {
  summary: string;
  summary_until_round: number;
  summary_updated_at: string;
  used_tokens_before: number;
  used_tokens_after: number;
  max_input_tokens: number;
  model_id: string;
}

// POST /sessions/{session_id}/compact 触发一键压缩
// 路径与 list/delete sessions 一致 不带 /api 前缀  vite 代理已覆盖 /sessions
// 错误码语义:
//   404 session 不存在
//   409 有进行中的 round
//   422 没有可压缩的 round
//   503 LLM 调用失败
// 这些错误统一以 Error.message 形式抛出  含 "HTTP 4xx/5xx: detail" 由调用方处理
export function compactSession(sessionId: string): Promise<CompactResponse> {
  return request<CompactResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/compact`,
    { method: 'POST' },
  );
}

// ====== 数字员工 agent 配置相关 API ======

// GET /api/agents 拉取所有 agent 与 judge 指向
export function getAgents(): Promise<AgentsListResponse> {
  return request<AgentsListResponse>('/api/agents');
}

// POST /api/models/discover 根据 Base URL + API Key 动态获取可用模型列表
export function discoverModels(
  body: DiscoverModelsRequest,
): Promise<DiscoverModelsResponse> {
  return request<DiscoverModelsResponse>('/api/models/discover', {
    method: 'POST',
    body,
  });
}

// POST /api/agents/{name}/models/discover 使用已有 agent 保存的 Key 拉模型列表
export function discoverAgentModels(
  name: string,
  body: Partial<DiscoverModelsRequest>,
): Promise<DiscoverModelsResponse> {
  return request<DiscoverModelsResponse>(
    `/api/agents/${encodeURIComponent(name)}/models/discover`,
    {
      method: 'POST',
      body,
    },
  );
}

// GET /api/agents/{name} 单个 agent
export async function getAgent(name: string): Promise<AgentView> {
  const data = await request<unknown>(`/api/agents/${encodeURIComponent(name)}`);
  return convertAgentView(data);
}

// POST /api/agents 创建一个新 agent api_key 必须明文
export async function createAgent(body: CreateAgentRequest): Promise<AgentView> {
  const data = await request<unknown>('/api/agents', {
    method: 'POST',
    body,
  });
  return convertAgentView(data);
}

// PUT /api/agents/{name} 更新 agent 不传 api_key 表示保留旧值
export async function updateAgent(
  name: string,
  body: UpdateAgentRequest,
): Promise<AgentView> {
  const data = await request<unknown>(`/api/agents/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body,
  });
  return convertAgentView(data);
}

// DELETE /api/agents/{name} 409 表示该 agent 是当前 judge_target
export function deleteAgent(name: string): Promise<void> {
  return requestNoContent(`/api/agents/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

// PUT /api/judge 切换 judge 指向哪个 agent 后端返回 204 无 body
export function updateJudge(target: string): Promise<void> {
  return requestNoContent('/api/judge', {
    method: 'PUT',
    body: { target },
  });
}

// POST /api/agents/{name}/avatar 上传头像
// 走 multipart/form-data 不能复用 request() 的 JSON 路径
// 后端限制 ≤ 2MB png/jpeg/webp/gif  超了走 413  mime 不对走 415
export async function uploadAgentAvatar(
  name: string,
  file: File,
): Promise<AgentView> {
  const form = new FormData();
  form.append('file', file, file.name);
  const resp = await fetch(
    `${BASE}/api/agents/${encodeURIComponent(name)}/avatar`,
    {
      method: 'POST',
      body: form,
    },
  );
  if (!resp.ok) {
    const detail = await extractErrorDetail(resp);
    throw new Error(`HTTP ${resp.status}: ${detail}`);
  }
  return convertAgentView(await resp.json());
}

// DELETE /api/agents/{name}/avatar 清掉头像 返回最新 AgentView
export async function deleteAgentAvatar(name: string): Promise<AgentView> {
  const data = await request<unknown>(
    `/api/agents/${encodeURIComponent(name)}/avatar`,
    { method: 'DELETE' },
  );
  return convertAgentView(data);
}

// ====== MCP 配置相关 API (单文档 JSON) ======

export interface McpConfigResponse {
  config: Record<string, unknown>;
}

export interface McpConfigRequest {
  config: Record<string, unknown>;
}

// GET /api/mcp/config 获取整份 MCP 配置 JSON
export function getMcpConfig(): Promise<McpConfigResponse> {
  return request<McpConfigResponse>('/api/mcp/config');
}

// PUT /api/mcp/config 全量覆盖 MCP 配置
export function putMcpConfig(body: McpConfigRequest): Promise<McpConfigResponse> {
  return request<McpConfigResponse>('/api/mcp/config', {
    method: 'PUT',
    body,
  });
}

// ====== MCP 服务器 CRUD API (表格管理模式) ======

export interface McpServerItem {
  name: string;
  transport: string;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  always_allow: string[];
  disabled: boolean;
  updated_at: string;
}

export interface McpServersListResponse {
  servers: McpServerItem[];
}

export interface McpServerUpdateRequest {
  transport: string;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  always_allow: string[];
  disabled: boolean;
}

export interface McpToggleRequest {
  disabled: boolean;
}

// GET /api/mcp/servers 列出所有 MCP 服务器
export function listMcpServers(): Promise<McpServersListResponse> {
  return request<McpServersListResponse>('/api/mcp/servers');
}

// POST /api/mcp/servers 新增一个 MCP 服务器
export function createMcpServer(body: McpServerItem): Promise<McpServerItem> {
  return request<McpServerItem>('/api/mcp/servers', {
    method: 'POST',
    body,
  });
}

// PUT /api/mcp/servers/{name} 修改单个 MCP 服务器
export function updateMcpServer(name: string, body: McpServerUpdateRequest): Promise<McpServerItem> {
  return request<McpServerItem>(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body,
  });
}

// DELETE /api/mcp/servers/{name} 删除单个 MCP 服务器
export function deleteMcpServer(name: string): Promise<void> {
  return requestNoContent(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

// PUT /api/mcp/servers/{name}/toggle 快捷启停开关
export function toggleMcpServer(name: string, body: McpToggleRequest): Promise<McpServerItem> {
  return request<McpServerItem>(`/api/mcp/servers/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body,
  });
}

// POST /api/mcp/reload 重载所有 agent 让 MCP 配置立即生效
// 与 /api/skills/reload 共享同一个后端入口  返回 { reloaded: N }
export interface McpReloadResponse {
  reloaded: number;
}

export function reloadMcpAgents(): Promise<McpReloadResponse> {
  return request<McpReloadResponse>('/api/mcp/reload', { method: 'POST' });
}

// ====== Skills 配置相关 API ======

export interface SkillItem {
  name: string;
  description: string;
  content: string;
  enabled: boolean;
}

export interface SkillsListResponse {
  skills: SkillItem[];
}

export interface SkillUpdateRequest {
  description: string;
  content: string;
  enabled: boolean;
}

export interface SkillToggleRequest {
  enabled: boolean;
}

// GET /api/skills 列出所有 skill
export function listSkills(): Promise<SkillsListResponse> {
  return request<SkillsListResponse>('/api/skills');
}

// POST /api/skills 新增一个 skill
export function createSkill(body: SkillItem): Promise<SkillItem> {
  return request<SkillItem>('/api/skills', {
    method: 'POST',
    body,
  });
}

// PUT /api/skills/{name} 修改单个 skill
export function updateSkill(name: string, body: SkillUpdateRequest): Promise<SkillItem> {
  return request<SkillItem>(`/api/skills/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body,
  });
}

// DELETE /api/skills/{name} 删除单个 skill
export function deleteSkill(name: string): Promise<void> {
  return requestNoContent(`/api/skills/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

// PUT /api/skills/{name}/toggle 快捷启停开关
export function toggleSkill(name: string, body: SkillToggleRequest): Promise<SkillItem> {
  return request<SkillItem>(`/api/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'PUT',
    body,
  });
}

// POST /api/skills/reload 重载所有 agent 使 skills 变更生效
export interface SkillsReloadResponse {
  reloaded: number;
}

export function reloadAgents(): Promise<SkillsReloadResponse> {
  return request<SkillsReloadResponse>('/api/skills/reload', {
    method: 'POST',
  });
}

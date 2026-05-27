// 后端 → 前端数据结构转换
// 后端返回 snake_case dict 字段名与前端 RoundView/SessionMeta 不一致
// 这里集中处理转换 让 useSession 和 App.bootstrap 共用同一份逻辑
import type {
  AgentName,
  AgentView,
  InputMode,
  ModelView,
  ReplySegment,
  ReplyState,
  ReplyView,
  RoundView,
  SessionMeta,
  TaskState,
  ToolCallEvent,
} from './types';

// /sessions 返回的 snake_case dict → SessionMeta
export function convertSession(raw: unknown): SessionMeta {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    sessionId: (r.session_id as string) ?? (r.sessionId as string) ?? '',
    title: (r.title as string) ?? '',
    updatedAt: (r.updated_at as string) ?? (r.updatedAt as string) ?? '',
    parentSessionId:
      (r.parent_session_id as string) ?? (r.parentSessionId as string) ?? null,
    branchFromTaskId:
      (r.branch_from_task_id as string) ?? (r.branchFromTaskId as string) ?? null,
    branchFromRole:
      ((r.branch_from_role as 'user' | 'assistant' | null) ??
        (r.branchFromRole as 'user' | 'assistant' | null) ??
        null),
    branchFromAgent:
      (r.branch_from_agent as string) ?? (r.branchFromAgent as string) ?? null,
    draftMessage:
      (r.draft_message as string) ?? (r.draftMessage as string) ?? null,
  };
}

// 把后端落库的 reply.segments 转成前端 ReplySegment[]
function convertSegments(raw: unknown): ReplySegment[] {
  if (!Array.isArray(raw)) return [];
  const out: ReplySegment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const seg = item as Record<string, unknown>;
    const type = seg.type;
    if (type === 'text') {
      out.push({ type: 'text', content: (seg.content as string) ?? '' });
    } else if (type === 'thinking') {
      out.push({ type: 'thinking', content: (seg.content as string) ?? '' });
    } else if (type === 'tool_call') {
      out.push({
        type: 'tool_call',
        tool: (seg.tool as string) ?? '',
        input: typeof seg.input === 'string' ? (seg.input as string) : undefined,
      });
    } else if (type === 'tool_result') {
      out.push({
        type: 'tool_result',
        tool: (seg.tool as string) ?? '',
        result: (seg.result as string) ?? '',
      });
    }
  }
  return out;
}

// 从 segments 反推 toolCalls 列表
function deriveToolCalls(segments: ReplySegment[]): ToolCallEvent[] {
  const calls: ToolCallEvent[] = [];
  for (const seg of segments) {
    if (seg.type === 'tool_call') {
      calls.push({ tool: seg.tool ?? '', input: seg.input });
    } else if (seg.type === 'tool_result') {
      const tool = seg.tool ?? '';
      let matched = false;
      for (let i = calls.length - 1; i >= 0; i--) {
        if (calls[i].tool === tool && !calls[i].result) {
          calls[i] = { ...calls[i], result: seg.result };
          matched = true;
          break;
        }
      }
      if (!matched) {
        calls.push({ tool, result: seg.result });
      }
    }
  }
  return calls;
}

// 把后端 replies dict (key=agent.name, value=reply 子字段) 转前端 Record<agent, ReplyView>
function convertReplies(
  raw: unknown,
  agents: AgentName[],
): Record<AgentName, ReplyView> {
  const out: Record<AgentName, ReplyView> = {};
  const dict = (raw ?? {}) as Record<string, unknown>;
  for (const agent of agents) {
    const r = (dict[agent] ?? {}) as Record<string, unknown>;
    const segments = convertSegments(r.segments);
    out[agent] = {
      agent,
      state: (r.state as ReplyState) ?? 'pending',
      content: (r.content as string) ?? '',
      toolCalls: deriveToolCalls(segments),
      segments,
      error: typeof r.error === 'string' ? (r.error as string) : undefined,
      finishedAt:
        typeof r.finished_at === 'string' ? (r.finished_at as string) : undefined,
    };
  }
  // dict 里可能还有 agents 列表之外的项(历史轮次或服务端兼容写入)  也一并塞进来
  for (const [agent, r] of Object.entries(dict)) {
    if (out[agent] !== undefined) continue;
    if (!r || typeof r !== 'object') continue;
    const rr = r as Record<string, unknown>;
    const segments = convertSegments(rr.segments);
    out[agent] = {
      agent,
      state: (rr.state as ReplyState) ?? 'pending',
      content: (rr.content as string) ?? '',
      toolCalls: deriveToolCalls(segments),
      segments,
      error: typeof rr.error === 'string' ? (rr.error as string) : undefined,
      finishedAt:
        typeof rr.finished_at === 'string' ? (rr.finished_at as string) : undefined,
    };
  }
  return out;
}

// /history 返回的 mongo round 文档 → RoundView
export function convertRound(raw: unknown): RoundView {
  const r = (raw ?? {}) as Record<string, unknown>;
  const stateRaw = String(r.state ?? 'done');
  const state = stateRaw.toUpperCase() as TaskState;

  // agents 列表  老数据没有就回退到 [reply.agent] (model_validator 已迁移到 replies)
  const agentsRaw = r.agents;
  let agents: AgentName[] = [];
  if (Array.isArray(agentsRaw)) {
    agents = agentsRaw.filter((x): x is string => typeof x === 'string');
  }
  // 如果 agents 为空但 replies 有  以 replies 的 key 兜底
  const repliesRaw = r.replies as Record<string, unknown> | undefined;
  if (agents.length === 0 && repliesRaw && typeof repliesRaw === 'object') {
    agents = Object.keys(repliesRaw);
  }

  const inputModeRaw = r.input_mode;
  const inputMode: InputMode =
    inputModeRaw === 'multi' ? 'multi' : 'single';

  const replies = convertReplies(repliesRaw ?? {}, agents);

  // 字段名兼容 早期 mongo 用 question 后改 user_message
  const userMessage =
    (r.user_message as string) ?? (r.question as string) ?? '';

  const selectedRaw = r.selected_reply_agent;
  const selectedReplyAgent =
    typeof selectedRaw === 'string' && selectedRaw ? (selectedRaw as AgentName) : null;

  return {
    taskId: (r.task_id as string) ?? (r.taskId as string) ?? '',
    state,
    userMessage,
    agents,
    inputMode,
    replies,
    selectedReplyAgent,
    createdAt: typeof r.created_at === 'string' ? (r.created_at as string) : undefined,
  };
}

// /api/agents 响应里单个 agent → AgentView
export function convertAgentView(raw: unknown): AgentView {
  const r = (raw ?? {}) as Record<string, unknown>;
  const ams = (r.available_models as unknown[]) ?? [];
  const available_models: ModelView[] = ams.map((m) => {
    const mr = (m ?? {}) as Record<string, unknown>;
    const tokensRaw = mr.max_input_tokens;
    const tokens =
      typeof tokensRaw === 'number' && tokensRaw > 0
        ? Math.floor(tokensRaw)
        : 200000;
    return {
      model_id: (mr.model_id as string) ?? '',
      label: (mr.label as string) ?? '',
      max_input_tokens: tokens,
    };
  });
  const avatarMetaRaw = r.avatar;
  const avatarMeta =
    avatarMetaRaw && typeof avatarMetaRaw === 'object'
      ? {
          object_key:
            ((avatarMetaRaw as Record<string, unknown>).object_key as string) ?? '',
          mime_type:
            ((avatarMetaRaw as Record<string, unknown>).mime_type as string) ?? '',
          size:
            (typeof (avatarMetaRaw as Record<string, unknown>).size === 'number'
              ? ((avatarMetaRaw as Record<string, unknown>).size as number)
              : 0),
          sha256:
            ((avatarMetaRaw as Record<string, unknown>).sha256 as string) ?? '',
        }
      : null;
  const avatarRaw = r.avatar_data_url;
  const avatarUrl = avatarMeta
    ? `/api/agents/${encodeURIComponent((r.name as string) ?? '')}/avatar?v=${encodeURIComponent(avatarMeta.sha256)}`
    : typeof avatarRaw === 'string' && avatarRaw
      ? avatarRaw
      : null;
  return {
    name: (r.name as string) ?? '',
    display_name:
      (r.display_name as string) ?? (r.name as string) ?? '',
    provider_type: (r.provider_type as string) ?? 'openai_compatible',
    base_url: (r.base_url as string) ?? '',
    api_key: (r.api_key as string) ?? '',
    model: (r.model as string) ?? '',
    available_models,
    prompt: (r.prompt as string) ?? '',
    version: (r.version as number) ?? 1,
    updated_at: (r.updated_at as string) ?? '',
    avatar: avatarMeta,
    avatar_data_url: avatarUrl,
  };
}

// 后端 → 前端数据结构转换
// 后端返回 snake_case dict 字段名与前端 RoundView/SessionMeta 不一致
// 这里集中处理转换 让 useSession 和 App.bootstrap 共用同一份逻辑
import type {
  AgentName,
  AgentView,
  ModelView,
  RoundView,
  SessionMeta,
  TaskState,
  ThinkState,
  ThinkView,
} from './types';

// /sessions 返回的 snake_case dict → SessionMeta
export function convertSession(raw: unknown): SessionMeta {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    sessionId: (r.session_id as string) ?? (r.sessionId as string) ?? '',
    title: (r.title as string) ?? '',
    updatedAt: (r.updated_at as string) ?? (r.updatedAt as string) ?? '',
  };
}

// /history 返回的 mongo round 文档 → RoundView
// 后端字段是 snake_case  reply 不带 toolCalls  state 小写枚举
// 兼容历史 mongo 数据中可能出现的旧字段名 question / reply_content 等
export function convertRound(raw: unknown): RoundView {
  const r = (raw ?? {}) as Record<string, unknown>;
  const stateRaw = String(r.state ?? 'done');
  const state = stateRaw.toUpperCase() as TaskState;

  // thinks 是 dict {agent_name: {state, content, error}}
  const thinksRaw = (r.thinks as Record<string, unknown> | undefined) ?? {};
  const thinks: Record<string, ThinkView> = {};
  for (const [agent, t] of Object.entries(thinksRaw)) {
    const tv = (t ?? {}) as Record<string, unknown>;
    thinks[agent] = {
      agent: agent as AgentName,
      state: (tv.state as ThinkState) ?? 'pending',
      content: tv.content as string | undefined,
      error: tv.error as string | undefined,
    };
  }

  // 字段名兼容 早期 mongo 用 question 后改 user_message
  const userMessage =
    (r.user_message as string) ?? (r.question as string) ?? '';

  // reply 字段补全 toolCalls 历史不存中间过程  一律为空数组
  // finishedAt 来自 round.reply.finished_at  历史轮次 reply 完成时落库
  const replyRaw = r.reply as Record<string, unknown> | undefined;
  const reply = replyRaw
    ? {
        agent: (replyRaw.agent as AgentName) ?? '',
        state:
          (replyRaw.state as 'streaming' | 'done' | 'failed' | 'cancelled') ??
          'done',
        content: (replyRaw.content as string) ?? '',
        toolCalls: [],
        segments: [],
        error: replyRaw.error as string | undefined,
        finishedAt: typeof replyRaw.finished_at === 'string' ? (replyRaw.finished_at as string) : undefined,
      }
    : undefined;

  // decision 字段名与前端一致 仅做类型 cast 避免 strict 报错
  const decisionRaw = r.decision as Record<string, unknown> | undefined;
  const decision = decisionRaw
    ? ({
        choice: (decisionRaw.choice as string) ?? '',
        reason: (decisionRaw.reason as string) ?? '',
      } as RoundView['decision'])
    : undefined;

  return {
    taskId: (r.task_id as string) ?? (r.taskId as string) ?? '',
    state,
    userMessage,
    thinks,
    decision,
    reply,
    createdAt: typeof r.created_at === 'string' ? (r.created_at as string) : undefined,
  };
}

// /api/agents 响应里单个 agent → AgentView
// 后端字段是 snake_case  这里做兼容性提取
export function convertAgentView(raw: unknown): AgentView {
  const r = (raw ?? {}) as Record<string, unknown>;
  const ams = (r.available_models as unknown[]) ?? [];
  const available_models: ModelView[] = ams.map((m) => {
    const mr = (m ?? {}) as Record<string, unknown>;
    return {
      model_id: (mr.model_id as string) ?? '',
      label: (mr.label as string) ?? '',
    };
  });
  const avatarRaw = r.avatar_data_url;
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
    avatar_data_url: typeof avatarRaw === 'string' && avatarRaw ? avatarRaw : null,
  };
}

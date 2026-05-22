// 后端 → 前端数据结构转换
// 后端返回 snake_case dict 字段名与前端 RoundView/SessionMeta 不一致
// 这里集中处理转换 让 useSession 和 App.bootstrap 共用同一份逻辑
import type {
  AgentName,
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
  const thinks = {} as Record<AgentName, ThinkView>;
  for (const [agent, t] of Object.entries(thinksRaw)) {
    const tv = (t ?? {}) as Record<string, unknown>;
    (thinks as Record<string, ThinkView>)[agent] = {
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
  const replyRaw = r.reply as Record<string, unknown> | undefined;
  const reply = replyRaw
    ? {
        agent: (replyRaw.agent as AgentName) ?? '',
        state:
          (replyRaw.state as 'streaming' | 'done' | 'failed' | 'cancelled') ??
          'done',
        content: (replyRaw.content as string) ?? '',
        toolCalls: [],
        error: replyRaw.error as string | undefined,
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
  };
}

// 后端 → 前端数据结构转换
// 后端返回 snake_case dict 字段名与前端 RoundView/SessionMeta 不一致
// 这里集中处理转换 让 useSession 和 App.bootstrap 共用同一份逻辑
import type {
  AgentName,
  AgentView,
  ModelView,
  ReplySegment,
  RoundView,
  SessionMeta,
  TaskState,
  ThinkState,
  ThinkView,
  ToolCallEvent,
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

// 把后端落库的 reply.segments 转成前端 ReplySegment[]
// 后端字段名与前端一致 (type / content / tool / input / result)  这里只做安全 cast
// 任何无法识别的段一律跳过 不抛异常 保证历史数据缺字段时也能渲染
function convertSegments(raw: unknown): ReplySegment[] {
  if (!Array.isArray(raw)) return [];
  const out: ReplySegment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const seg = item as Record<string, unknown>;
    const type = seg.type;
    if (type === 'text') {
      out.push({ type: 'text', content: (seg.content as string) ?? '' });
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

// 从 segments 反推 toolCalls 列表  老组件按 toolCalls 数组渲染
// 同名工具按时间顺序配对 tool_call → tool_result  没匹配上的 tool_result 单独追加一项
// 与 reducer 里 reply.tool_call / reply.tool_result 实时合并的逻辑保持一致
function deriveToolCalls(segments: ReplySegment[]): ToolCallEvent[] {
  const calls: ToolCallEvent[] = [];
  for (const seg of segments) {
    if (seg.type === 'tool_call') {
      calls.push({ tool: seg.tool ?? '', input: seg.input });
    } else if (seg.type === 'tool_result') {
      const tool = seg.tool ?? '';
      // 从后往前找第一个同名且没填 result 的 tool_call  填 result
      let matched = false;
      for (let i = calls.length - 1; i >= 0; i--) {
        if (calls[i].tool === tool && !calls[i].result) {
          calls[i] = { ...calls[i], result: seg.result };
          matched = true;
          break;
        }
      }
      if (!matched) {
        // 没匹配上的孤儿 result  保留为独立项 避免丢信息
        calls.push({ tool, result: seg.result });
      }
    }
  }
  return calls;
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

  // reply 还原  segments 来自后端 reply.segments  老数据里没这字段就给空数组
  // toolCalls 从 segments 反推  保证刷新页面后历史轮次工具调用按时间顺序还原
  // finishedAt 来自 round.reply.finished_at  历史轮次 reply 完成时落库
  const replyRaw = r.reply as Record<string, unknown> | undefined;
  const segments = replyRaw ? convertSegments(replyRaw.segments) : [];
  const toolCalls = deriveToolCalls(segments);
  const reply = replyRaw
    ? {
        agent: (replyRaw.agent as AgentName) ?? '',
        state:
          (replyRaw.state as 'streaming' | 'done' | 'failed' | 'cancelled') ??
          'done',
        content: (replyRaw.content as string) ?? '',
        toolCalls,
        segments,
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
    // max_input_tokens 老数据可能没字段 兜底 200000  后端读取层也会兜底
    // 此处与 backend/src/multichat/storage/mongo.py _agent_doc_to_record 保持一致
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

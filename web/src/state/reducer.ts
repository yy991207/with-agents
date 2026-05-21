// Chat 状态 reducer:集中处理所有 action
// M4 扩展:把 SSE 事件分发到 round 视图;支持 history 加载、session 切换

import type {
  AgentEditDraft,
  AgentName,
  AgentView,
  ChatAction,
  ChatState,
  DecisionView,
  ReplyView,
  RoundView,
  SettingsState,
  SSEEvent,
  TaskState,
  ThinkState,
  ThinkView,
  ToolCallEvent,
} from './types';
import { KNOWN_AGENTS } from './types';

// Settings 子树初始值:抽屉关闭、无草稿
const initialSettings: SettingsState = {
  open: false,
  loading: false,
  saving: false,
  drafts: {},
  judgeTarget: null,
};

// 初始状态
export const initialState: ChatState = {
  sessionId: null,
  sessions: [],
  rounds: [],
  activeTaskId: null,
  taskState: 'PENDING',
  sseStatus: 'idle',
  settings: initialSettings,
};

// 工具:在 rounds 数组中替换某个 taskId 的 round
function patchRound(
  rounds: RoundView[],
  taskId: string,
  patch: Partial<RoundView>,
): RoundView[] {
  return rounds.map((r) => (r.taskId === taskId ? { ...r, ...patch } : r));
}

// 工具:把后端返回的 agent 列表展平成 drafts 字典,初始 dirty=false
function buildDrafts(agents: AgentView[]): Record<string, AgentEditDraft> {
  const out: Record<string, AgentEditDraft> = {};
  for (const a of agents) {
    out[a.name] = {
      name: a.name,
      model: a.model,
      prompt: a.prompt,
      version: a.version,
      dirty: false,
    };
  }
  return out;
}

// ====== SSE 事件分发辅助 ======

// 安全读取 data 中的字段(后端 snake_case,这里把常用字段都尝试一遍)
function readString(data: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const k of keys) {
    const v = data[k];
    if (typeof v === 'string') return v;
  }
  return undefined;
}

function readAgent(data: Record<string, unknown>, ...keys: string[]): AgentName | undefined {
  const s = readString(data, ...keys);
  if (s && (KNOWN_AGENTS as string[]).includes(s)) return s as AgentName;
  return undefined;
}

function readAgentList(data: Record<string, unknown>, ...keys: string[]): AgentName[] | undefined {
  for (const k of keys) {
    const v = data[k];
    if (Array.isArray(v)) {
      const list = v.filter((x): x is AgentName => typeof x === 'string' && (KNOWN_AGENTS as string[]).includes(x));
      return list;
    }
  }
  return undefined;
}

// 在某个 round 上更新 thinks[agent]
function updateThink(round: RoundView, agent: AgentName, patch: Partial<ThinkView>): RoundView {
  const cur = round.thinks[agent] ?? { agent, state: 'pending' as ThinkState };
  return {
    ...round,
    thinks: {
      ...round.thinks,
      [agent]: { ...cur, ...patch },
    },
  };
}

// 把单条 SSE 事件应用到当前活跃 round 上
// 注意:这里假设 activeTaskId 指向的 round 一定存在;不存在则原样返回
function applySSEEvent(state: ChatState, event: SSEEvent): ChatState {
  const taskId = state.activeTaskId;
  if (!taskId) return state;
  const idx = state.rounds.findIndex((r) => r.taskId === taskId);
  if (idx < 0) return state;
  const round = state.rounds[idx];
  const data = event.data ?? {};

  // 工具:把变更后的 round 写回 rounds,并可附带顶层 taskState 修改
  const apply = (next: RoundView, taskState?: TaskState): ChatState => {
    const rounds = [...state.rounds];
    rounds[idx] = next;
    return {
      ...state,
      rounds,
      taskState: taskState ?? state.taskState,
    };
  };

  switch (event.type) {
    case 'task.state': {
      const s = readString(data, 'state') as TaskState | undefined;
      if (!s) return state;
      // 顺带把 available_agents / judge_pick / decision 落到 round
      const patch: Partial<RoundView> = { state: s };
      const avail = readAgentList(data, 'available_agents', 'availableAgents');
      if (avail) patch.availableAgents = avail;
      const judge = readAgent(data, 'judge_pick', 'judgePick');
      if (judge) patch.judgePick = judge;
      const decisionRaw = data['decision'];
      if (decisionRaw && typeof decisionRaw === 'object') {
        const dr = decisionRaw as Record<string, unknown>;
        const choice = readString(dr, 'choice');
        const reason = readString(dr, 'reason') ?? '';
        if (choice) {
          // choice 可能是 'auto' / 'regenerate' / AgentName,这里统一断言
          patch.decision = {
            choice: choice as DecisionView['choice'],
            reason,
          };
        }
      }
      const cancelReason = readString(data, 'reason');
      if (cancelReason) patch.cancelReason = cancelReason;
      return apply({ ...round, ...patch }, s);
    }

    case 'think.start': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      return apply(updateThink(round, agent, { state: 'pending', error: undefined }));
    }

    case 'think.done': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const content = readString(data, 'content') ?? '';
      return apply(updateThink(round, agent, { state: 'done', content, error: undefined }));
    }

    case 'think.failed': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const error = readString(data, 'error', 'message') ?? '未知错误';
      return apply(updateThink(round, agent, { state: 'failed', error }));
    }

    case 'think.cancelled': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      return apply(updateThink(round, agent, { state: 'cancelled' }));
    }

    case 'judge.start': {
      // judge 进行中暂不持久化;可由 UI 用临时态展示
      return state;
    }

    case 'judge.done': {
      const chosen = readAgent(data, 'chosen', 'agent');
      if (!chosen) return state;
      return apply({ ...round, judgePick: chosen });
    }

    case 'reply.start': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const reply: ReplyView = {
        agent,
        state: 'streaming',
        content: '',
        toolCalls: [],
      };
      return apply({ ...round, reply }, 'REPLYING');
    }

    case 'reply.chunk': {
      const agent = readAgent(data, 'agent');
      const chunk = readString(data, 'chunk', 'content') ?? '';
      if (!round.reply || !agent) return state;
      const reply: ReplyView = {
        ...round.reply,
        agent,
        content: round.reply.content + chunk,
        state: 'streaming',
      };
      return apply({ ...round, reply });
    }

    case 'reply.tool_call': {
      const tool = readString(data, 'tool', 'name');
      const input = readString(data, 'input', 'arguments');
      if (!tool || !round.reply) return state;
      const call: ToolCallEvent = { tool, input };
      const reply: ReplyView = {
        ...round.reply,
        toolCalls: [...round.reply.toolCalls, call],
      };
      return apply({ ...round, reply });
    }

    case 'reply.tool_result': {
      const tool = readString(data, 'tool', 'name');
      const result = readString(data, 'result', 'output') ?? '';
      if (!tool || !round.reply) return state;
      // 倒序找最后一个同名且 result 还没填的工具调用
      const calls = [...round.reply.toolCalls];
      for (let i = calls.length - 1; i >= 0; i--) {
        if (calls[i].tool === tool && !calls[i].result) {
          calls[i] = { ...calls[i], result };
          break;
        }
      }
      const reply: ReplyView = { ...round.reply, toolCalls: calls };
      return apply({ ...round, reply });
    }

    case 'reply.done': {
      if (!round.reply) return state;
      // 后端会给完整 content,优先采用;没给就保留累积值
      const content = readString(data, 'content');
      const reply: ReplyView = {
        ...round.reply,
        state: 'done',
        content: content ?? round.reply.content,
      };
      return apply({ ...round, reply, state: 'DONE' }, 'DONE');
    }

    case 'reply.error': {
      if (!round.reply) return state;
      const error = readString(data, 'error', 'message') ?? '未知错误';
      const reply: ReplyView = { ...round.reply, state: 'failed', error };
      return apply({ ...round, reply });
    }

    case 'task.unrecoverable': {
      const reason = readString(data, 'reason', 'message') ?? '任务异常终止';
      return apply({ ...round, state: 'CANCELLED', cancelReason: reason }, 'CANCELLED');
    }

    case 'snapshot': {
      // 重连场景:服务端把已经发过的事件再播放一遍
      const events = data['events'];
      if (!Array.isArray(events)) return state;
      let next = state;
      for (const raw of events) {
        if (!raw || typeof raw !== 'object') continue;
        const ev = raw as Record<string, unknown>;
        const type = readString(ev, 'type');
        if (!type) continue;
        const inner = (ev['data'] as Record<string, unknown>) ?? {};
        next = applySSEEvent(next, { type, data: inner });
      }
      return next;
    }

    default:
      return state;
  }
}

// 主 reducer
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'session.set':
      return { ...state, sessionId: action.sessionId };

    case 'session.switch':
      // 切换会话时清空 rounds 与活跃任务,等 history.loaded 重新填回
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: [],
        activeTaskId: null,
        taskState: 'PENDING',
        sseStatus: 'idle',
      };

    case 'sessions.set':
      return { ...state, sessions: action.sessions };

    case 'rounds.set':
      return { ...state, rounds: action.rounds };

    case 'history.loaded':
      // 历史落地后:rounds 替换;若新会话有未结束任务,activeTaskId 保留为最后一轮
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: action.rounds,
        activeTaskId: null,
        taskState: 'DONE',
      };

    case 'round.append':
      return { ...state, rounds: [...state.rounds, action.round] };

    case 'round.update':
      return {
        ...state,
        rounds: patchRound(state.rounds, action.taskId, action.patch),
      };

    case 'task.created':
      // 创建新任务时同时落 sessionId 与 activeTaskId,塞一个空 round 占位
      return {
        ...state,
        sessionId: action.sessionId,
        activeTaskId: action.taskId,
        taskState: 'PENDING',
        rounds: [...state.rounds, createEmptyRound(action.taskId, action.userMessage)],
      };

    case 'task.resume': {
      // 抗刷新重连:把 activeTaskId 设回去,等 snapshot 回放
      // 若 history 已带回该 round(后端 mongo 已落库),保留原 round 不动
      // 若没有(很少见,但可能 history 还没写库就刷新了),塞个空占位防止 snapshot 落空
      const exists = state.rounds.some((r) => r.taskId === action.taskId);
      const rounds = exists
        ? state.rounds
        : [...state.rounds, createEmptyRound(action.taskId, '')];
      return {
        ...state,
        activeTaskId: action.taskId,
        taskState: action.taskState ?? 'PENDING',
        rounds,
      };
    }

    case 'task.state':
      return { ...state, taskState: action.state };

    case 'sse.status':
      return { ...state, sseStatus: action.status };

    case 'sse.event':
      // 把 SSE 事件按 type 分发到 round 视图
      return applySSEEvent(state, action.event);

    // ====== 配置抽屉相关 ======
    case 'settings.open':
      return { ...state, settings: { ...state.settings, open: true } };

    case 'settings.close':
      return {
        ...state,
        settings: { ...state.settings, open: false, saving: false, loading: false },
      };

    case 'settings.loading.start':
      return { ...state, settings: { ...state.settings, loading: true } };

    case 'settings.loaded':
      return {
        ...state,
        settings: {
          ...state.settings,
          loading: false,
          drafts: buildDrafts(action.agents),
          judgeTarget: action.judgeTarget,
        },
      };

    case 'settings.draft.update': {
      const cur = state.settings.drafts[action.name];
      if (!cur) return state;
      const next: AgentEditDraft = {
        ...cur,
        [action.field]: action.value,
        dirty: true,
      };
      return {
        ...state,
        settings: {
          ...state.settings,
          drafts: { ...state.settings.drafts, [action.name]: next },
        },
      };
    }

    case 'settings.saving.start':
      return { ...state, settings: { ...state.settings, saving: true } };

    case 'settings.saved': {
      const cur = state.settings.drafts[action.name];
      if (!cur) {
        return { ...state, settings: { ...state.settings, saving: false } };
      }
      const next: AgentEditDraft = {
        ...cur,
        version: action.version,
        dirty: false,
      };
      return {
        ...state,
        settings: {
          ...state.settings,
          saving: false,
          drafts: { ...state.settings.drafts, [action.name]: next },
        },
      };
    }

    case 'settings.judge.set':
      return {
        ...state,
        settings: { ...state.settings, judgeTarget: action.target },
      };

    case 'settings.error':
      return {
        ...state,
        settings: { ...state.settings, loading: false, saving: false },
      };

    default:
      return state;
  }
}

// 创建一个空 round,4 个 think 都是 pending(供 task.created 与外部使用)
export function createEmptyRound(taskId: string, userMessage: string): RoundView {
  const empty: Record<AgentName, ThinkView> = {
    DeepSeek: { agent: 'DeepSeek', state: 'pending' },
    GLM: { agent: 'GLM', state: 'pending' },
    Kimi: { agent: 'Kimi', state: 'pending' },
    Qwen: { agent: 'Qwen', state: 'pending' },
  };
  return {
    taskId,
    state: 'PENDING',
    userMessage,
    thinks: empty,
  };
}

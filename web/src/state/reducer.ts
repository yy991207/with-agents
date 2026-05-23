// Chat 状态 reducer:集中处理所有 action
// 数字员工模型重构:agent 数量动态 settings 子树砍掉 profile 池

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

// Settings 子树初始值:抽屉关闭 无草稿
const initialSettings: SettingsState = {
  open: false,
  loading: false,
  saving: false,
  drafts: {},
  judgeTarget: null,
  activeAgentName: null,
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

// 工具:把后端返回的 agent 视图展平成本地 draft
function viewToDraft(a: AgentView): AgentEditDraft {
  return {
    name: a.name,
    displayName: a.display_name || a.name,
    providerType: a.provider_type || 'openai_compatible',
    baseUrl: a.base_url,
    // 填回后端 mask 值（如 sk-...a43d），Input.Password 自动渲染成黑圆点。
    // apiKeyDirty=false 保证保存时不发送 mask，不会覆盖真实 key。
    apiKey: a.api_key || '',
    apiKeyDirty: false,
    apiKeyMask: a.api_key || '',
    model: a.model,
    availableModels: a.available_models ?? [],
    prompt: a.prompt,
    version: a.version,
    dirty: false,
  };
}

// 工具:把后端返回的 agent 列表展平成 drafts 字典 初始 dirty=false
function buildDrafts(agents: AgentView[]): Record<string, AgentEditDraft> {
  const out: Record<string, AgentEditDraft> = {};
  for (const a of agents) {
    out[a.name] = viewToDraft(a);
  }
  return out;
}

// ====== SSE 事件分发辅助 ======

// 安全读取 data 中的字段(后端 snake_case 这里把常用字段都尝试一遍)
function readString(data: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const k of keys) {
    const v = data[k];
    if (typeof v === 'string') return v;
  }
  return undefined;
}

// agent 字段不再校验固定 union 仅取字符串
function readAgent(data: Record<string, unknown>, ...keys: string[]): AgentName | undefined {
  return readString(data, ...keys);
}

function readAgentList(data: Record<string, unknown>, ...keys: string[]): AgentName[] | undefined {
  for (const k of keys) {
    const v = data[k];
    if (Array.isArray(v)) {
      const list = v.filter((x): x is AgentName => typeof x === 'string');
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

// 把单条 SSE 事件应用到对应 round 上。
// SharedWorker 会同时托管多个 task 的连接,所以必须优先使用事件来源 taskId,
// 不能只依赖 activeTaskId,否则旧任务回放会被写到当前活跃轮次。
function applySSEEvent(
  state: ChatState,
  event: SSEEvent,
  sourceTaskId?: string,
): ChatState {
  const taskId = sourceTaskId ?? state.activeTaskId;
  if (!taskId) return state;
  const idx = state.rounds.findIndex((r) => r.taskId === taskId);
  if (idx < 0) return state;
  const round = state.rounds[idx];
  const data = event.data ?? {};

  // 工具:把变更后的 round 写回 rounds 并可附带顶层 taskState 修改
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
      // judge 进行中暂不持久化 可由 UI 用临时态展示
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
        next = applySSEEvent(next, { type, data: inner }, taskId);
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
      // 切换会话时清空 rounds 与活跃任务 等 history.loaded 重新填回
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: [],
        activeTaskId: null,
        taskState: 'PENDING',
        sseStatus: 'idle',
      };

    case 'session.deleted': {
      // 删除会话:从 sessions 列表移除 若删的就是当前会话 清空对话视图
      const sessions = state.sessions.filter((s) => s.sessionId !== action.sessionId);
      const isCurrent = state.sessionId === action.sessionId;
      if (isCurrent) {
        return {
          ...state,
          sessions,
          sessionId: null,
          rounds: [],
          activeTaskId: null,
          taskState: 'DONE',
          sseStatus: 'idle',
        };
      }
      return { ...state, sessions };
    }

    case 'sessions.set':
      return { ...state, sessions: action.sessions };

    case 'rounds.set':
      return { ...state, rounds: action.rounds };

    case 'history.loaded':
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

    case 'task.created': {
      // 创建新任务时同时落 sessionId 与 activeTaskId 塞一个空 round 占位
      const isNewSession = !state.sessions.some(
        (s) => s.sessionId === action.sessionId,
      );
      const updatedSessions = isNewSession
        ? [
            {
              sessionId: action.sessionId,
              title: action.userMessage.slice(0, 40) || '新会话',
              updatedAt: new Date().toISOString(),
            },
            ...state.sessions,
          ]
        : state.sessions;
      return {
        ...state,
        sessionId: action.sessionId,
        activeTaskId: action.taskId,
        taskState: 'PENDING',
        rounds: [...state.rounds, createEmptyRound(action.taskId, action.userMessage, state.settings.drafts)],
        sessions: updatedSessions,
      };
    }

    case 'task.resume': {
      // 抗刷新重连:把 activeTaskId 设回去 等 snapshot 回放
      const exists = state.rounds.some((r) => r.taskId === action.taskId);
      const rounds = exists
        ? state.rounds
        : [...state.rounds, createEmptyRound(action.taskId, '', state.settings.drafts)];
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
      return applySSEEvent(state, action.event, action.taskId);

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

    case 'settings.loaded': {
      const drafts = buildDrafts(action.agents);
      // 默认选中第一个 agent 作为 active tab 若已有 active 且仍存在 则保留
      const keys = Object.keys(drafts);
      let activeAgentName = state.settings.activeAgentName;
      if (!activeAgentName || !drafts[activeAgentName]) {
        activeAgentName = keys[0] ?? null;
      }
      return {
        ...state,
        settings: {
          ...state.settings,
          loading: false,
          drafts,
          judgeTarget: action.judgeTarget,
          activeAgentName,
        },
      };
    }

    case 'settings.draft.field': {
      const cur = state.settings.drafts[action.agentName];
      if (!cur) return state;
      // patch 中如果包含 apiKey 同步把 apiKeyDirty 标 true
      const merged: AgentEditDraft = {
        ...cur,
        ...action.patch,
        // dirty 永远拉成 true 避免外部 patch 误覆盖成 false
        dirty: true,
        apiKeyDirty:
          'apiKey' in action.patch ? true : cur.apiKeyDirty,
      };
      return {
        ...state,
        settings: {
          ...state.settings,
          drafts: { ...state.settings.drafts, [action.agentName]: merged },
        },
      };
    }

    case 'settings.saving.start':
      return { ...state, settings: { ...state.settings, saving: true } };

    case 'settings.saved': {
      // 用服务端返回的最新 agent 重置该 draft
      const next = viewToDraft(action.agent);
      return {
        ...state,
        settings: {
          ...state.settings,
          saving: false,
          drafts: { ...state.settings.drafts, [action.agent.name]: next },
        },
      };
    }

    case 'settings.judge.set':
      return {
        ...state,
        settings: { ...state.settings, judgeTarget: action.target },
      };

    case 'settings.agent.created': {
      // 新增 agent 转 draft 并自动切到该 tab
      const next = viewToDraft(action.agent);
      return {
        ...state,
        settings: {
          ...state.settings,
          drafts: { ...state.settings.drafts, [action.agent.name]: next },
          activeAgentName: action.agent.name,
        },
      };
    }

    case 'settings.agent.deleted': {
      const drafts = { ...state.settings.drafts };
      delete drafts[action.name];
      const keys = Object.keys(drafts);
      // 若被删的是当前 active 自动切到第一个剩余
      const activeAgentName =
        state.settings.activeAgentName === action.name
          ? keys[0] ?? null
          : state.settings.activeAgentName;
      // 若被删的是当前 judge 清空 由后端配合切换 这里仅做本地展示兜底
      const judgeTarget =
        state.settings.judgeTarget === action.name
          ? null
          : state.settings.judgeTarget;
      return {
        ...state,
        settings: {
          ...state.settings,
          drafts,
          activeAgentName,
          judgeTarget,
        },
      };
    }

    case 'settings.agent.tab.switch':
      return {
        ...state,
        settings: { ...state.settings, activeAgentName: action.name },
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

// 创建一个空 round 所有已知 agent 的 think 都是 pending
// drafts 用于决定初始 thinks 的 key 集合 没有 drafts 也允许返回空 thinks
export function createEmptyRound(
  taskId: string,
  userMessage: string,
  drafts?: Record<string, AgentEditDraft>,
): RoundView {
  const empty: Record<string, ThinkView> = {};
  if (drafts) {
    for (const name of Object.keys(drafts)) {
      empty[name] = { agent: name, state: 'pending' };
    }
  }
  return {
    taskId,
    state: 'PENDING',
    userMessage,
    thinks: empty,
  };
}

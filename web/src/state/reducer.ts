// Chat 状态 reducer:集中处理所有 action
// reply 渲染按 segments 时间线顺序展示文本与工具调用
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
  WorkbenchState,
} from './types';

const initialSettings: SettingsState = {
  open: false,
  loading: false,
  saving: false,
  drafts: {},
  judgeTarget: null,
  activeAgentName: null,
};

const initialWorkbench: WorkbenchState = {
  activeView: 'home',
  sidebarCollapsed: false,
  recentExpanded: true,
  agentsExpanded: true,
  recommendPage: 0,
};

export const initialState: ChatState = {
  sessionId: null,
  sessions: [],
  rounds: [],
  activeTaskId: null,
  taskState: 'PENDING',
  sseStatus: 'idle',
  settings: initialSettings,
  workbench: initialWorkbench,
};

function patchRound(rounds: RoundView[], taskId: string, patch: Partial<RoundView>): RoundView[] {
  return rounds.map((r) => (r.taskId === taskId ? { ...r, ...patch } : r));
}

function viewToDraft(a: AgentView): AgentEditDraft {
  return {
    name: a.name,
    displayName: a.display_name || a.name,
    providerType: a.provider_type || 'openai_compatible',
    baseUrl: a.base_url,
    apiKey: a.api_key || '',
    apiKeyDirty: false,
    apiKeyMask: a.api_key || '',
    model: a.model,
    availableModels: a.available_models ?? [],
    prompt: a.prompt,
    version: a.version,
    dirty: false,
    avatarDataUrl: a.avatar_data_url ?? null,
  };
}

function buildDrafts(agents: AgentView[]): Record<string, AgentEditDraft> {
  const out: Record<string, AgentEditDraft> = {};
  for (const a of agents) {
    out[a.name] = viewToDraft(a);
  }
  return out;
}

function readString(data: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const k of keys) {
    const v = data[k];
    if (typeof v === 'string') return v;
  }
  return undefined;
}

function readAgent(data: Record<string, unknown>, ...keys: string[]): AgentName | undefined {
  return readString(data, ...keys);
}

function readAgentList(data: Record<string, unknown>, ...keys: string[]): AgentName[] | undefined {
  for (const k of keys) {
    const v = data[k];
    if (Array.isArray(v)) {
      return v.filter((x): x is AgentName => typeof x === 'string');
    }
  }
  return undefined;
}

function updateThink(round: RoundView, agent: AgentName, patch: Partial<ThinkView>): RoundView {
  const cur = round.thinks[agent] ?? { agent, state: 'pending' as ThinkState };
  return { ...round, thinks: { ...round.thinks, [agent]: { ...cur, ...patch } } };
}

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

  const apply = (next: RoundView, taskState?: TaskState): ChatState => {
    const rounds = [...state.rounds];
    rounds[idx] = next;
    return { ...state, rounds, taskState: taskState ?? state.taskState };
  };

  switch (event.type) {
    case 'task.state': {
      const s = readString(data, 'state') as TaskState | undefined;
      if (!s) return state;
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
        if (choice) patch.decision = { choice: choice as DecisionView['choice'], reason };
      }
      const cancelReason = readString(data, 'reason');
      if (cancelReason) patch.cancelReason = cancelReason;
      // 取消时同步把 reply 也标为 cancelled 让 UI 立即停止 loading
      if (s === 'CANCELLED' && round.reply && round.reply.state !== 'done') {
        patch.reply = { ...round.reply, state: 'cancelled' as ReplyView['state'] };
      }
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
    case 'judge.start':
      return state;
    case 'judge.done': {
      const chosen = readAgent(data, 'chosen', 'agent');
      if (!chosen) return state;
      return apply({ ...round, judgePick: chosen });
    }

    // === reply 阶段: segments 时间线 ===
    case 'reply.start': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const reply: ReplyView = {
        agent,
        state: 'streaming',
        content: '',
        toolCalls: [],
        segments: [],
      };
      return apply({ ...round, reply }, 'REPLYING');
    }

    case 'reply.chunk': {
      const agent = readAgent(data, 'agent');
      const chunk = readString(data, 'chunk', 'content') ?? '';
      if (!round.reply || !agent) return state;
      const segments = [...round.reply.segments];
      const last = segments[segments.length - 1];
      if (last && last.type === 'text') {
        segments[segments.length - 1] = { ...last, content: (last.content ?? '') + chunk };
      } else {
        segments.push({ type: 'text', content: chunk });
      }
      const reply: ReplyView = {
        ...round.reply,
        agent,
        content: round.reply.content + chunk,
        segments,
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
        segments: [...round.reply.segments, { type: 'tool_call' as const, tool, input }],
      };
      return apply({ ...round, reply });
    }

    case 'reply.tool_result': {
      const tool = readString(data, 'tool', 'name');
      const result = readString(data, 'result', 'output') ?? '';
      if (!tool || !round.reply) return state;
      const calls = [...round.reply.toolCalls];
      for (let i = calls.length - 1; i >= 0; i--) {
        if (calls[i].tool === tool && !calls[i].result) {
          calls[i] = { ...calls[i], result };
          break;
        }
      }
      const reply: ReplyView = {
        ...round.reply,
        toolCalls: calls,
        segments: [...round.reply.segments, { type: 'tool_result' as const, tool, result }],
      };
      return apply({ ...round, reply });
    }

    case 'reply.done': {
      if (!round.reply) return state;
      const content = readString(data, 'content');
      const finishedAt = readString(data, 'finished_at');
      const reply: ReplyView = {
        ...round.reply,
        state: 'done',
        content: content ?? round.reply.content,
        finishedAt: finishedAt ?? round.reply.finishedAt,
      };
      return apply({ ...round, reply, state: 'DONE' }, 'DONE');
    }
    case 'reply.error': {
      if (!round.reply) return state;
      const error = readString(data, 'error', 'message') ?? '未知错误';
      return apply({ ...round, reply: { ...round.reply, state: 'failed', error } });
    }
    case 'task.unrecoverable': {
      const reason = readString(data, 'reason', 'message') ?? '任务异常终止';
      return apply({ ...round, state: 'CANCELLED', cancelReason: reason }, 'CANCELLED');
    }
    case 'snapshot': {
      const events = data['events'];
      if (!Array.isArray(events)) return state;
      let next = state;
      for (const raw of events) {
        if (!raw || typeof raw !== 'object') continue;
        const ev = raw as Record<string, unknown>;
        const type = readString(ev, 'type');
        if (!type) continue;
        next = applySSEEvent(next, { type, data: (ev['data'] as Record<string, unknown>) ?? {} }, taskId);
      }
      return next;
    }
    default:
      return state;
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'session.set': return { ...state, sessionId: action.sessionId };
    case 'session.switch':
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: [],
        activeTaskId: null,
        taskState: 'PENDING',
        sseStatus: 'idle',
        workbench: {
          ...state.workbench,
          activeView: action.sessionId ? 'chat' : 'home',
        },
      };
    case 'session.deleted': {
      const sessions = state.sessions.filter((s) => s.sessionId !== action.sessionId);
      if (state.sessionId === action.sessionId)
        return {
          ...state,
          sessions,
          sessionId: null,
          rounds: [],
          activeTaskId: null,
          taskState: 'DONE',
          sseStatus: 'idle',
          workbench: {
            ...state.workbench,
            activeView: 'home',
          },
        };
      return { ...state, sessions };
    }
    case 'sessions.set': return { ...state, sessions: action.sessions };
    case 'rounds.set': return { ...state, rounds: action.rounds };
    case 'history.loaded':
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: action.rounds,
        activeTaskId: null,
        taskState: 'DONE',
        workbench: {
          ...state.workbench,
          activeView:
            action.rounds.length > 0 || state.workbench.activeView === 'chat'
              ? 'chat'
              : 'home',
        },
      };
    case 'round.append': return { ...state, rounds: [...state.rounds, action.round] };
    case 'round.update':
      return { ...state, rounds: patchRound(state.rounds, action.taskId, action.patch) };
    case 'task.created': {
      const isNewSession = !state.sessions.some((s) => s.sessionId === action.sessionId);
      const updatedSessions = isNewSession
        ? [{ sessionId: action.sessionId, title: action.userMessage.slice(0, 40) || '新会话', updatedAt: new Date().toISOString() }, ...state.sessions]
        : state.sessions;
      return {
        ...state,
        sessionId: action.sessionId,
        activeTaskId: action.taskId,
        taskState: 'PENDING',
        rounds: [...state.rounds, createEmptyRound(action.taskId, action.userMessage, state.settings.drafts, action.createdAt)],
        sessions: updatedSessions,
        workbench: {
          ...state.workbench,
          activeView: 'chat',
        },
      };
    }
    case 'task.resume': {
      const exists = state.rounds.some((r) => r.taskId === action.taskId);
      const rounds = exists ? state.rounds : [...state.rounds, createEmptyRound(action.taskId, '', state.settings.drafts)];
      return { ...state, activeTaskId: action.taskId, taskState: action.taskState ?? 'PENDING', rounds };
    }
    case 'task.state': return { ...state, taskState: action.state };
    case 'sse.status': return { ...state, sseStatus: action.status };
    case 'sse.event': return applySSEEvent(state, action.event, action.taskId);

    case 'ui.view.set':
      return {
        ...state,
        workbench: {
          ...state.workbench,
          activeView: action.view,
        },
      };
    case 'ui.sidebar.toggle':
      return {
        ...state,
        workbench: {
          ...state.workbench,
          sidebarCollapsed: action.collapsed ?? !state.workbench.sidebarCollapsed,
        },
      };
    case 'ui.section.toggle':
      return {
        ...state,
        workbench: {
          ...state.workbench,
          recentExpanded:
            action.section === 'recent'
              ? !state.workbench.recentExpanded
              : state.workbench.recentExpanded,
          agentsExpanded:
            action.section === 'agents'
              ? !state.workbench.agentsExpanded
              : state.workbench.agentsExpanded,
        },
      };
    case 'ui.recommend.rotate':
      return {
        ...state,
        workbench: {
          ...state.workbench,
          recommendPage: state.workbench.recommendPage + 1,
        },
      };

    case 'settings.open': return { ...state, settings: { ...state.settings, open: true } };
    case 'settings.close':
      return { ...state, settings: { ...state.settings, open: false, saving: false, loading: false } };
    case 'settings.loading.start': return { ...state, settings: { ...state.settings, loading: true } };
    case 'settings.loaded': {
      const drafts = buildDrafts(action.agents);
      const keys = Object.keys(drafts);
      let activeAgentName = state.settings.activeAgentName;
      if (!activeAgentName || !drafts[activeAgentName]) activeAgentName = keys[0] ?? null;
      return { ...state, settings: { ...state.settings, loading: false, drafts, judgeTarget: action.judgeTarget, activeAgentName } };
    }
    case 'settings.draft.field': {
      const cur = state.settings.drafts[action.agentName];
      if (!cur) return state;
      const merged: AgentEditDraft = {
        ...cur, ...action.patch, dirty: true,
        apiKeyDirty: 'apiKey' in action.patch ? true : cur.apiKeyDirty,
      };
      return { ...state, settings: { ...state.settings, drafts: { ...state.settings.drafts, [action.agentName]: merged } } };
    }
    case 'settings.saving.start': return { ...state, settings: { ...state.settings, saving: true } };
    case 'settings.saved': {
      const next = viewToDraft(action.agent);
      return { ...state, settings: { ...state.settings, saving: false, drafts: { ...state.settings.drafts, [action.agent.name]: next } } };
    }
    case 'settings.judge.set': return { ...state, settings: { ...state.settings, judgeTarget: action.target } };
    case 'settings.agent.created': {
      const next = viewToDraft(action.agent);
      return { ...state, settings: { ...state.settings, drafts: { ...state.settings.drafts, [action.agent.name]: next }, activeAgentName: action.agent.name } };
    }
    case 'settings.agent.deleted': {
      const drafts = { ...state.settings.drafts };
      delete drafts[action.name];
      const keys = Object.keys(drafts);
      return {
        ...state,
        settings: {
          ...state.settings, drafts,
          activeAgentName: state.settings.activeAgentName === action.name ? (keys[0] ?? null) : state.settings.activeAgentName,
          judgeTarget: state.settings.judgeTarget === action.name ? null : state.settings.judgeTarget,
        },
      };
    }
    case 'settings.agent.tab.switch': return { ...state, settings: { ...state.settings, activeAgentName: action.name } };
    // 头像上传/删除直连 mongo  这里只把 server 返回的 avatarDataUrl 同步到本地 draft
    // 不动 dirty / version  保留用户其它字段的编辑稿
    case 'settings.agent.avatar.set': {
      const cur = state.settings.drafts[action.agentName];
      if (!cur) return state;
      const next: AgentEditDraft = { ...cur, avatarDataUrl: action.avatarDataUrl };
      return {
        ...state,
        settings: {
          ...state.settings,
          drafts: { ...state.settings.drafts, [action.agentName]: next },
        },
      };
    }
    case 'settings.error': return { ...state, settings: { ...state.settings, loading: false, saving: false } };
    default: return state;
  }
}

export function createEmptyRound(
  taskId: string,
  userMessage: string,
  drafts?: Record<string, AgentEditDraft>,
  createdAt?: string,
): RoundView {
  const empty: Record<string, ThinkView> = {};
  if (drafts) {
    for (const name of Object.keys(drafts)) empty[name] = { agent: name, state: 'pending' };
  }
  return { taskId, state: 'PENDING', userMessage, thinks: empty, createdAt };
}

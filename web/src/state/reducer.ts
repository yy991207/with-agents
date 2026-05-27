// Chat 状态 reducer:集中处理所有 action
// 多 agent 并发回答模型: round.replies[agent] 各自独立  按 SSE event 的 agent 字段路由
import type {
  AgentEditDraft,
  AgentName,
  AgentView,
  ChatAction,
  ChatState,
  ContextUsage,
  InputMode,
  ReplySegment,
  ReplyState,
  ReplyView,
  RoundView,
  SettingsState,
  SSEEvent,
  TaskState,
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
  chatLayout: 'single',
  chatPanes: [{ key: 'primary', sessionId: null }],
};

export const initialState: ChatState = {
  currentUser: null,
  sessionId: null,
  sessions: [],
  rounds: [],
  activeTaskId: null,
  taskState: 'PENDING',
  sseStatus: 'idle',
  settings: initialSettings,
  workbench: initialWorkbench,
  contextUsage: null,
  compacting: false,
  fullscreenReply: null,
  sessionDraftMessage: null,
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
    // 后端现在返回完整 api_key 原文 (不再 mask)  apiKey 字段直接展示给用户编辑
    // apiKeyMask 字段保留 仅作 placeholder/help 文案的判定依据 (是否已设置过 key)
    apiKey: a.api_key || '',
    apiKeyDirty: false,
    apiKeyMask: a.api_key || '',
    model: a.model,
    availableModels: a.available_models ?? [],
    prompt: a.prompt,
    version: a.version,
    dirty: false,
    avatar: a.avatar ?? null,
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

// 解析 SSE context.usage 事件 payload
export function parseContextUsage(data: Record<string, unknown>): ContextUsage | null {
  const used = data['used_tokens'];
  const threshold = data['threshold_tokens'];
  const maxInput = data['max_input_tokens'];
  const ratio = data['ratio'];
  const modelId = data['model_id'];
  if (
    typeof used !== 'number' ||
    typeof threshold !== 'number' ||
    typeof maxInput !== 'number' ||
    typeof ratio !== 'number' ||
    typeof modelId !== 'string'
  ) {
    return null;
  }
  return {
    used_tokens: used,
    threshold_tokens: threshold,
    max_input_tokens: maxInput,
    ratio,
    model_id: modelId,
  };
}

// 把单个 agent 的 reply 视图按字段补丁更新  其他 agent 不受影响
function patchReply(
  round: RoundView,
  agent: AgentName,
  patch: Partial<ReplyView>,
): RoundView {
  const cur =
    round.replies[agent] ??
    {
      agent,
      state: 'pending' as ReplyState,
      content: '',
      toolCalls: [],
      segments: [],
    };
  return {
    ...round,
    replies: { ...round.replies, [agent]: { ...cur, ...patch, agent } },
  };
}

function applySSEEvent(
  state: ChatState,
  event: SSEEvent,
  sourceTaskId?: string,
): ChatState {
  // context.usage 是会话级事件  不挂在某轮 round 上
  if (event.type === 'context.usage') {
    const data = event.data ?? {};
    const usage = parseContextUsage(data);
    if (!usage) return state;
    return { ...state, contextUsage: usage };
  }

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
      // 后端 fan-out 时会带 agents 列表  挂回 round 防止刷新后丢
      const agents = readAgentList(data, 'agents');
      if (agents) patch.agents = agents;
      const cancelReason = readString(data, 'reason');
      if (cancelReason) patch.cancelReason = cancelReason;
      // 整轮取消时  把所有还在 streaming/pending 的 reply 标 cancelled
      if (s === 'CANCELLED') {
        const replies = { ...round.replies };
        for (const [agent, reply] of Object.entries(replies)) {
          if (reply && reply.state !== 'done' && reply.state !== 'failed') {
            replies[agent] = { ...reply, state: 'cancelled' as ReplyState };
          }
        }
        patch.replies = replies;
      }
      return apply({ ...round, ...patch }, s);
    }

    // === reply 阶段: 各 agent 独立  按事件 agent 字段路由 ===
    case 'reply.start': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const next = patchReply(round, agent, {
        state: 'streaming',
        content: '',
        toolCalls: [],
        segments: [],
        error: undefined,
        finishedAt: undefined,
      });
      return apply(next, 'REPLYING');
    }

    case 'reply.chunk': {
      const agent = readAgent(data, 'agent');
      const chunk = readString(data, 'chunk', 'content') ?? '';
      if (!agent) return state;
      const cur = round.replies[agent];
      if (!cur) return state;
      const segments = [...cur.segments];
      const last = segments[segments.length - 1];
      if (last && last.type === 'text') {
        segments[segments.length - 1] = { ...last, content: (last.content ?? '') + chunk };
      } else {
        segments.push({ type: 'text', content: chunk });
      }
      return apply(
        patchReply(round, agent, {
          content: cur.content + chunk,
          segments,
          state: 'streaming',
        }),
      );
    }

    case 'reply.thinking': {
      const agent = readAgent(data, 'agent');
      const chunk = readString(data, 'chunk', 'content') ?? '';
      if (!agent || !chunk) return state;
      const cur = round.replies[agent];
      if (!cur) return state;
      const segments = [...cur.segments];
      const last = segments[segments.length - 1];
      if (last && last.type === 'thinking') {
        segments[segments.length - 1] = { ...last, content: (last.content ?? '') + chunk };
      } else {
        segments.push({ type: 'thinking', content: chunk });
      }
      return apply(
        patchReply(round, agent, { segments, state: 'streaming' }),
      );
    }

    case 'reply.tool_call': {
      const agent = readAgent(data, 'agent');
      const tool = readString(data, 'tool', 'name');
      const input = readString(data, 'input', 'arguments');
      if (!agent || !tool) return state;
      const cur = round.replies[agent];
      if (!cur) return state;
      const call: ToolCallEvent = { tool, input };
      return apply(
        patchReply(round, agent, {
          toolCalls: [...cur.toolCalls, call],
          segments: [
            ...cur.segments,
            { type: 'tool_call' as const, tool, input } as ReplySegment,
          ],
        }),
      );
    }

    case 'reply.tool_result': {
      const agent = readAgent(data, 'agent');
      const tool = readString(data, 'tool', 'name');
      const result = readString(data, 'result', 'output') ?? '';
      if (!agent || !tool) return state;
      const cur = round.replies[agent];
      if (!cur) return state;
      const calls = [...cur.toolCalls];
      for (let i = calls.length - 1; i >= 0; i--) {
        if (calls[i].tool === tool && !calls[i].result) {
          calls[i] = { ...calls[i], result };
          break;
        }
      }
      return apply(
        patchReply(round, agent, {
          toolCalls: calls,
          segments: [
            ...cur.segments,
            { type: 'tool_result' as const, tool, result } as ReplySegment,
          ],
        }),
      );
    }

    case 'reply.done': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const cur = round.replies[agent];
      if (!cur) return state;
      const content = readString(data, 'content');
      const finishedAt = readString(data, 'finished_at');
      return apply(
        patchReply(round, agent, {
          state: 'done',
          content: content ?? cur.content,
          finishedAt: finishedAt ?? cur.finishedAt,
        }),
      );
    }

    case 'reply.error': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      const cur = round.replies[agent];
      if (!cur) return state;
      const error = readString(data, 'error', 'message') ?? '未知错误';
      const isCancel = error === 'cancelled';
      return apply(
        patchReply(round, agent, {
          state: isCancel ? 'cancelled' : 'failed',
          error: isCancel ? undefined : error,
        }),
      );
    }

    case 'reply.selected': {
      const agent = readAgent(data, 'agent');
      if (!agent) return state;
      return apply({ ...round, selectedReplyAgent: agent });
    }

    case 'task.unrecoverable': {
      const reason = readString(data, 'reason', 'message', 'error') ?? '任务异常终止';
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
    case 'auth.current_user.set':
      return { ...state, currentUser: action.user };
    case 'session.set': return { ...state, sessionId: action.sessionId };
    case 'session.switch':
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: [],
        activeTaskId: null,
        // 切会话只是等待历史接口返回  不是在发新任务
        // 这里保持 DONE 避免 Timeline 抢先渲染默认空会话占位页
        taskState: 'DONE',
        sseStatus: 'idle',
        contextUsage: null,
        compacting: false,
        fullscreenReply: null,
        sessionDraftMessage: null,
        workbench: {
          ...state.workbench,
          activeView: action.sessionId ? 'chat' : 'home',
          chatLayout: action.sessionId ? state.workbench.chatLayout : 'single',
          chatPanes:
            action.sessionId === null
              ? [{ key: 'primary', sessionId: null }]
              : state.workbench.chatLayout === 'single'
                ? [{ key: 'primary', sessionId: action.sessionId }]
                : state.workbench.chatPanes,
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
          contextUsage: null,
          compacting: false,
          fullscreenReply: null,
          sessionDraftMessage: null,
          workbench: {
            ...state.workbench,
            activeView: 'home',
            chatLayout: 'single',
            chatPanes: [{ key: 'primary', sessionId: null }],
          },
        };
      return {
        ...state,
        sessions,
        workbench: {
          ...state.workbench,
          chatPanes: state.workbench.chatPanes.map((pane) =>
            pane.sessionId === action.sessionId ? { ...pane, sessionId: null } : pane,
          ),
        },
      };
    }
    case 'sessions.set': return { ...state, sessions: action.sessions };
    case 'session.draft.set':
      return { ...state, sessionDraftMessage: action.draftMessage };
    case 'rounds.set': return { ...state, rounds: action.rounds };
    case 'history.loaded':
      return {
        ...state,
        sessionId: action.sessionId,
        rounds: action.rounds,
        activeTaskId: null,
        taskState: 'DONE',
        contextUsage: action.contextUsage ?? null,
        compacting: false,
        fullscreenReply: null,
        sessionDraftMessage: action.draftMessage ?? null,
        workbench: {
          ...state.workbench,
          activeView:
            action.rounds.length > 0 || state.workbench.activeView === 'chat'
              ? 'chat'
              : 'home',
          chatPanes:
            state.workbench.chatLayout === 'single'
              ? [{ key: 'primary', sessionId: action.sessionId }]
              : state.workbench.chatPanes,
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
      // 切到一个不同的 session 时  旧 rounds 必须清掉
      // 否则首页"新建会话"发完  Timeline 会同时显示旧会话历史 + 这条新 round  视觉错乱
      const switchedSession = state.sessionId !== action.sessionId;
      const baseRounds = switchedSession ? [] : state.rounds;
      const editingIdx =
        !switchedSession && action.replaceTaskId
          ? baseRounds.findIndex((round) => round.taskId === action.replaceTaskId)
          : -1;
      const nextRound = createEmptyRound(
        action.taskId,
        action.userMessage,
        action.agents,
        action.inputMode,
        action.createdAt,
      );
      const preservedRounds =
        editingIdx >= 0 ? baseRounds.slice(0, editingIdx) : baseRounds;
      const nextRounds = [...preservedRounds, nextRound];
      return {
        ...state,
        sessionId: action.sessionId,
        activeTaskId: action.taskId,
        taskState: 'PENDING',
        rounds: nextRounds,
        // 切会话同时清 contextUsage  避免显示旧会话的 token 用量
        contextUsage:
          switchedSession || editingIdx >= 0 ? null : state.contextUsage,
        sessionDraftMessage: null,
        sessions: updatedSessions,
        workbench: {
          ...state.workbench,
          activeView: 'chat',
          chatPanes:
            state.workbench.chatLayout === 'single'
              ? [{ key: 'primary', sessionId: action.sessionId }]
              : state.workbench.chatPanes,
        },
      };
    }
    case 'task.resume': {
      const exists = state.rounds.some((r) => r.taskId === action.taskId);
      const rounds = exists ? state.rounds : [...state.rounds, createEmptyRound(action.taskId, '', [], 'single')];
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
    case 'ui.chat.layout.set':
      return {
        ...state,
        workbench: {
          ...state.workbench,
          chatLayout: action.layout,
        },
      };
    case 'ui.chat.panes.set':
      return {
        ...state,
        workbench: {
          ...state.workbench,
          chatPanes: action.panes,
          chatLayout: action.layout ?? state.workbench.chatLayout,
        },
      };
    case 'ui.fullscreen.set':
      return { ...state, fullscreenReply: action.fullscreen };
    case 'ui.fullscreen.agent.set':
      if (
        !state.fullscreenReply ||
        state.fullscreenReply.taskId !== action.taskId
      ) {
        return state;
      }
      return {
        ...state,
        fullscreenReply: {
          ...state.fullscreenReply,
          agent: action.agent,
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

    case 'context.usage': return { ...state, contextUsage: action.usage };
    case 'compact.start': return { ...state, compacting: true };
    case 'compact.done': return { ...state, compacting: false, contextUsage: action.usage };
    case 'compact.fail': return { ...state, compacting: false };

    default: return state;
  }
}

// 创建一个空 RoundView 占位  task.created 时调用
// agents 已知时按列表初始化 replies 占位  让 UI 立即显示骨架
export function createEmptyRound(
  taskId: string,
  userMessage: string,
  agents: AgentName[],
  inputMode: InputMode,
  createdAt?: string,
): RoundView {
  const replies: Record<AgentName, ReplyView> = {};
  for (const name of agents) {
    replies[name] = {
      agent: name,
      state: 'pending',
      content: '',
      toolCalls: [],
      segments: [],
    };
  }
  return {
    taskId,
    state: 'PENDING',
    userMessage,
    agents: [...agents],
    inputMode,
    replies,
    selectedReplyAgent: null,
    createdAt,
  };
}

// 为外部模块提供构建空 segments 的辅助  当前未直接消费  保留扩展点
export function emptySegments(): ReplySegment[] {
  return [];
}

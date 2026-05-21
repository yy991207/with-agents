// Chat 状态 reducer:集中处理所有 action
import type {
  AgentEditDraft,
  AgentView,
  ChatAction,
  ChatState,
  RoundView,
  SettingsState,
} from './types';

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

// 主 reducer
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'session.set':
      return { ...state, sessionId: action.sessionId };

    case 'sessions.set':
      return { ...state, sessions: action.sessions };

    case 'rounds.set':
      return { ...state, rounds: action.rounds };

    case 'round.append':
      return { ...state, rounds: [...state.rounds, action.round] };

    case 'round.update':
      return {
        ...state,
        rounds: patchRound(state.rounds, action.taskId, action.patch),
      };

    case 'task.created':
      return {
        ...state,
        activeTaskId: action.taskId,
        taskState: 'PENDING',
      };

    case 'task.state':
      return { ...state, taskState: action.state };

    case 'sse.status':
      return { ...state, sseStatus: action.status };

    case 'sse.event':
      // SSE 事件的细节归并放在 hook 里处理,这里只做 sse 状态记录占位
      // 真实业务流接入时,会拆分到具体 round.update / task.state 等 action
      return state;

    // ====== 配置抽屉相关 ======
    case 'settings.open':
      return { ...state, settings: { ...state.settings, open: true } };

    case 'settings.close':
      // 关闭抽屉时不清空草稿,留着上次的状态便于二次打开;真正清理由 loaded 覆盖
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
      if (!cur) return state; // 没有这个 agent 的草稿,直接忽略
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
        // 即使本地没草稿也要把 saving 落回去
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
      // 仅清掉 loading/saving 标记,具体提示由调用方用 message.error 弹出
      return {
        ...state,
        settings: { ...state.settings, loading: false, saving: false },
      };

    default:
      return state;
  }
}

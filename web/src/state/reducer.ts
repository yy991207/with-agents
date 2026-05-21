// Chat 状态 reducer:集中处理所有 action
import type { ChatAction, ChatState, RoundView } from './types';

// 初始状态
export const initialState: ChatState = {
  sessionId: null,
  sessions: [],
  rounds: [],
  activeTaskId: null,
  taskState: 'PENDING',
  sseStatus: 'idle',
};

// 工具:在 rounds 数组中替换某个 taskId 的 round
function patchRound(
  rounds: RoundView[],
  taskId: string,
  patch: Partial<RoundView>,
): RoundView[] {
  return rounds.map((r) => (r.taskId === taskId ? { ...r, ...patch } : r));
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

    default:
      return state;
  }
}

// 会话 hook:封装会话列表、切换会话与拉历史
import { useCallback, useEffect } from 'react';
import { getHistory, listSessions } from '../api/http';
import { useChat } from '../state/ChatContext';

export function useSession() {
  const { state, dispatch } = useChat();

  // 拉会话列表
  const refreshSessions = useCallback(async () => {
    const list = await listSessions();
    dispatch({ type: 'sessions.set', sessions: list });
  }, [dispatch]);

  // 切到指定 session 并加载历史
  const switchSession = useCallback(
    async (sessionId: string | null) => {
      dispatch({ type: 'session.set', sessionId });
      if (sessionId) {
        const rounds = await getHistory(sessionId);
        dispatch({ type: 'rounds.set', rounds });
      } else {
        dispatch({ type: 'rounds.set', rounds: [] });
      }
    },
    [dispatch],
  );

  // 首次挂载拉一下会话列表(占位,真实 UI 时机可能不同)
  useEffect(() => {
    refreshSessions().catch(() => {
      // 拉失败暂不抛,避免空骨架运行时崩溃
    });
  }, [refreshSessions]);

  return {
    sessions: state.sessions,
    sessionId: state.sessionId,
    refreshSessions,
    switchSession,
  };
}

// 会话 hook:封装会话列表、切换会话与拉历史
// /history 返回 { session, rounds[] },与 M3 后端契约一致
// H1 改造:切换会话时主动关闭旧 SSE,避免旧任务事件继续落到新会话视图
import { useCallback, useEffect } from 'react';
import { message } from 'antd';
import { getHistory, listSessions } from '../api/http';
import { useChat } from '../state/ChatContext';

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export function useSession() {
  const { state, dispatch, closeSSEController } = useChat();

  // 拉会话列表
  const refreshSessions = useCallback(async (): Promise<void> => {
    try {
      const list = await listSessions();
      dispatch({ type: 'sessions.set', sessions: list });
    } catch (e) {
      // 列表拉失败不影响主流程,仅提示
      message.warning(`会话列表加载失败:${describeError(e)}`);
    }
  }, [dispatch]);

  // 切到指定 session 并加载历史
  const switchSession = useCallback(
    async (sessionId: string | null): Promise<void> => {
      // 先把旧 SSE 关掉,防止旧 round 的事件继续往新会话上写
      closeSSEController();
      // 重置当前视图,等 history.loaded 重新填回
      dispatch({ type: 'session.switch', sessionId });
      if (!sessionId) return;
      try {
        const resp = await getHistory(sessionId);
        dispatch({ type: 'history.loaded', sessionId, rounds: resp.rounds });
      } catch (e) {
        message.error(`加载历史失败:${describeError(e)}`);
      }
    },
    [closeSSEController, dispatch],
  );

  // 首次挂载拉一下会话列表(占位,真实 UI 时机可能不同)
  useEffect(() => {
    refreshSessions().catch(() => {
      // refreshSessions 内部已经 toast 过,这里兜底吞异常
    });
  }, [refreshSessions]);

  return {
    sessions: state.sessions,
    sessionId: state.sessionId,
    refreshSessions,
    switchSession,
  };
}

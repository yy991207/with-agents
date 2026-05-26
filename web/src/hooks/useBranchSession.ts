import { message } from 'antd';
import { branchSession, getHistory, listSessions } from '../api/http';
import { useChat } from '../state/ChatContext';
import { convertRound, convertSession } from '../state/converters';
import { parseContextUsage } from '../state/reducer';

export function useBranchSession() {
  const { state, dispatch } = useChat();

  const branch = async (input: {
    taskId: string;
    role: 'user' | 'assistant';
    agent?: string;
  }) => {
    if (!state.sessionId) return;
    if (state.taskState === 'PENDING' || state.taskState === 'REPLYING') {
      message.warning('当前还有回复进行中，请先暂停后再创建分支');
      return;
    }
    try {
      const resp = await branchSession(state.sessionId, {
        source_task_id: input.taskId,
        source_role: input.role,
        source_agent: input.agent,
      });
      const hist = await getHistory(resp.session_id);
      const rounds = (hist.rounds as unknown as unknown[]).map(convertRound);
      const sessRaw = (hist.session ?? {}) as unknown as Record<string, unknown>;
      const usageRaw = sessRaw['context_usage'];
      const contextUsage =
        usageRaw && typeof usageRaw === 'object'
          ? parseContextUsage(usageRaw as Record<string, unknown>)
          : null;
      dispatch({
        type: 'history.loaded',
        sessionId: resp.session_id,
        rounds,
        contextUsage,
        draftMessage:
          typeof sessRaw['draft_message'] === 'string'
            ? (sessRaw['draft_message'] as string)
            : (resp.draft_message ?? null),
      });
      try {
        const metas = await listSessions();
        dispatch({
          type: 'sessions.set',
          sessions: (metas as unknown as unknown[]).map(convertSession),
        });
      } catch {
        // ignore
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      message.error(`创建分支失败:${msg}`);
    }
  };

  return { branch };
}

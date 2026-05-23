// 单轮任务 hook:封装 ask -> openSSE 的发起流程,并暴露决策、取消、重试 think
// stop 流程：先调后端 cancel 再本地 dispatch CANCELLED，确保 UI 立刻更新
import { useCallback } from 'react';
import { message } from 'antd';
import { ask, cancel, decide, retryThink } from '../api/http';
import { openTaskStream } from '../api/sse';
import { useChat } from '../state/ChatContext';
import type { AgentName } from '../state/types';

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

function isFatalSSEError(err: unknown): boolean {
  const msg = describeError(err);
  return msg.includes('404') || msg.includes('410');
}

export function useChatTask() {
  const { state, dispatch, registerSSEController, closeSSEController } = useChat();

  const send = useCallback(
    async (rawMessage: string): Promise<void> => {
      const trimmed = rawMessage.trim();
      if (!trimmed) return;

      try {
        const { task_id, session_id } = await ask({
          session_id: state.sessionId ?? undefined,
          user_message: trimmed,
        });

        dispatch({
          type: 'task.created',
          sessionId: session_id,
          taskId: task_id,
          userMessage: trimmed,
        });

        const ctrl = new AbortController();
        registerSSEController(ctrl);
        void openTaskStream(task_id, dispatch, {
          signal: ctrl.signal,
          onFatal: (err) => {
            const reason = isFatalSSEError(err)
              ? '任务在服务端不可恢复'
              : `连接异常 ${describeError(err)}`;
            dispatch({
              type: 'sse.event',
              taskId: task_id,
              event: { type: 'task.unrecoverable', data: { reason } },
            });
          },
        });
      } catch (e) {
        message.error(`提交失败:${describeError(e)}`);
      }
    },
    [dispatch, registerSSEController, state.sessionId],
  );

  // 主动停止: 先通知后端取消 再本地立即标记 确保 UI 不卡在 loading
  const stop = useCallback(async (): Promise<void> => {
    const taskId = state.activeTaskId;
    if (!taskId) return;
    // 先告知后端
    try {
      await cancel({ task_id: taskId, scope: 'global' });
    } catch (e) {
      message.error(`取消失败:${describeError(e)}`);
    }
    // 关闭 SSE 连接
    closeSSEController();
    // 前端本地立即标记取消 SSE 已断后端事件推不过来
    dispatch({
      type: 'sse.event',
      taskId,
      event: { type: 'task.state', data: { state: 'CANCELLED', reason: 'user_cancel' } },
    });
  }, [closeSSEController, state.activeTaskId, dispatch]);

  const decideChoice = useCallback(
    async (choice: AgentName | 'auto' | 'regenerate'): Promise<void> => {
      if (!state.activeTaskId) return;
      try {
        await decide({ task_id: state.activeTaskId, choice });
      } catch (e) {
        message.error(`决策失败:${describeError(e)}`);
      }
    },
    [state.activeTaskId],
  );

  const cancelAgent = useCallback(
    async (agent: AgentName): Promise<void> => {
      if (!state.activeTaskId) return;
      try {
        await cancel({ task_id: state.activeTaskId, scope: agent });
      } catch (e) {
        message.error(`取消 ${agent} 失败:${describeError(e)}`);
      }
    },
    [state.activeTaskId],
  );

  const retryAgent = useCallback(
    async (agent: AgentName): Promise<void> => {
      if (!state.activeTaskId) return;
      try {
        await retryThink({ task_id: state.activeTaskId, agent });
        message.success(`已请求重试 ${agent}`);
      } catch (e) {
        const msg = describeError(e);
        if (msg.includes('501')) {
          message.warning('单 agent 重试暂未实装,可整体重新发问');
        } else {
          message.error(`重试失败:${msg}`);
        }
      }
    },
    [state.activeTaskId],
  );

  return { send, stop, decideChoice, cancelAgent, retryAgent };
}

export { isFatalSSEError };

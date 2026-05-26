// 单轮任务 hook:封装 ask -> openSSE 的发起流程,并暴露选答、取消、重答接口
// stop 流程：先调后端 cancel 再本地 dispatch CANCELLED，确保 UI 立刻更新
import { useCallback } from 'react';
import { message } from 'antd';
import { ask, cancel, retryReply, selectReply } from '../api/http';
import { openTaskStream } from '../api/sse';
import { useChat } from '../state/ChatContext';
import type { AgentName, InputMode } from '../state/types';

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

function isFatalSSEError(err: unknown): boolean {
  const msg = describeError(err);
  return msg.includes('404') || msg.includes('410');
}

export interface SendOptions {
  thinking?: boolean;
  agents: AgentName[];
  inputMode: InputMode;
}

export function useChatTask() {
  const { state, dispatch, registerSSEController, closeSSEController } = useChat();

  const send = useCallback(
    async (rawMessage: string, options: SendOptions): Promise<void> => {
      const trimmed = rawMessage.trim();
      if (!trimmed) return;
      if (!options.agents.length) {
        message.error('请选择至少一个回答的 agent');
        return;
      }

      try {
        const { task_id, session_id, created_at } = await ask({
          session_id: state.sessionId ?? undefined,
          user_message: trimmed,
          agents: options.agents,
          input_mode: options.inputMode,
          thinking: options.thinking === true ? true : undefined,
        });

        dispatch({
          type: 'task.created',
          sessionId: session_id,
          taskId: task_id,
          userMessage: trimmed,
          agents: options.agents,
          inputMode: options.inputMode,
          createdAt: created_at,
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

  // 主动停止: 先通知后端取消整个任务 再本地立即标记
  const stop = useCallback(async (): Promise<void> => {
    const taskId = state.activeTaskId;
    if (!taskId) return;
    try {
      await cancel({ task_id: taskId, scope: 'global' });
    } catch (e) {
      message.error(`取消失败:${describeError(e)}`);
    }
    closeSSEController();
    dispatch({
      type: 'sse.event',
      taskId,
      event: { type: 'task.state', data: { state: 'CANCELLED', reason: 'user_cancel' } },
    });
  }, [closeSSEController, state.activeTaskId, dispatch]);

  // 取消单个 agent 的 reply 子任务  其他 agent 不动
  const cancelReplyAgent = useCallback(
    async (taskId: string, agent: AgentName): Promise<void> => {
      try {
        await cancel({ task_id: taskId, scope: agent });
      } catch (e) {
        message.error(`取消 ${agent} 失败:${describeError(e)}`);
      }
    },
    [],
  );

  // 重答单个 agent  会清空 selected_reply_agent 让用户重新选答
  const retryReplyAgent = useCallback(
    async (taskId: string, agent: AgentName): Promise<void> => {
      try {
        await retryReply({ task_id: taskId, agent });
      } catch (e) {
        message.error(`重答 ${agent} 失败:${describeError(e)}`);
      }
    },
    [],
  );

  // 用户从多 agent 候选中选定一个作为正式回答
  // 后端 task 已结束时 hub 已 close  publish 不出 reply.selected 事件
  // 这里成功后直接乐观派发一个 reply.selected event 到 reducer  立刻刷新 UI
  // 不依赖 SSE 回灌  避免选答 chip 点了没反应
  const selectReplyAgent = useCallback(
    async (taskId: string, agent: AgentName): Promise<void> => {
      try {
        await selectReply({ task_id: taskId, agent });
        dispatch({
          type: 'sse.event',
          taskId,
          event: { type: 'reply.selected', data: { agent } },
        });
      } catch (e) {
        message.error(`选答失败:${describeError(e)}`);
      }
    },
    [dispatch],
  );

  return { send, stop, cancelReplyAgent, retryReplyAgent, selectReplyAgent };
}

export { isFatalSSEError };

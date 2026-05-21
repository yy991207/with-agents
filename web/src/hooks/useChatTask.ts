// 单轮任务 hook:封装 ask -> openSSE 的发起流程,并暴露决策、取消、重试 think
import { useCallback, useRef } from 'react';
import { message } from 'antd';
import { ask, cancel, decide, retryThink } from '../api/http';
import { openTaskStream } from '../api/sse';
import { useChat } from '../state/ChatContext';
import type { AgentName } from '../state/types';

// 抽出错误信息的人话描述
function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export function useChatTask() {
  const { state, dispatch } = useChat();
  // 用 ref 持有 AbortController,便于外部停止 SSE
  const abortRef = useRef<AbortController | null>(null);

  // 发问入口
  const send = useCallback(
    async (rawMessage: string): Promise<void> => {
      const trimmed = rawMessage.trim();
      if (!trimmed) return;

      try {
        // 1. 调 /ask 拿到 task_id 与 session_id
        const { task_id, session_id } = await ask({
          session_id: state.sessionId ?? undefined,
          user_message: trimmed,
        });

        // 2. 把空 round 落到 state(reducer 内部会塞)
        dispatch({
          type: 'task.created',
          sessionId: session_id,
          taskId: task_id,
          userMessage: trimmed,
        });

        // 3. 打开 SSE 流(失败由 sse.ts 内部处理)
        abortRef.current?.abort();
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        // 不 await,避免阻塞 UI;Promise 在 SSE 关闭后才 resolve
        void openTaskStream(task_id, dispatch, {
          signal: ctrl.signal,
          onFatal: (err) => {
            message.error(`SSE 异常:${describeError(err)}`);
          },
        });
      } catch (e) {
        message.error(`提交失败:${describeError(e)}`);
      }
    },
    [dispatch, state.sessionId],
  );

  // 主动关闭当前流(全局停止按钮 + 通知后端取消任务)
  const stop = useCallback(async (): Promise<void> => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!state.activeTaskId) return;
    try {
      await cancel({ task_id: state.activeTaskId, scope: 'global' });
    } catch (e) {
      message.error(`取消失败:${describeError(e)}`);
    }
  }, [state.activeTaskId]);

  // 用户在 DecisionCard 里选了一个 agent / auto / regenerate
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

  // 取消单个 agent 的 think(不打断整个 task)
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

  // 重试某个 agent 的 think:M2 暂未实装,后端 501 → 给提示
  const retryAgent = useCallback(
    async (agent: AgentName): Promise<void> => {
      if (!state.activeTaskId) return;
      try {
        await retryThink({ task_id: state.activeTaskId, agent });
        message.success(`已请求重试 ${agent}`);
      } catch (e) {
        const msg = describeError(e);
        // 后端 501 表示功能暂未实装
        if (msg.includes('501')) {
          message.warning('单 agent 重试暂未实装,可整体重新发问');
        } else {
          message.error(`重试失败:${msg}`);
        }
      }
    },
    [state.activeTaskId],
  );

  return {
    send,
    stop,
    decideChoice,
    cancelAgent,
    retryAgent,
  };
}

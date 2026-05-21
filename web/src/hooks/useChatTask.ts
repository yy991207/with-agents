// 单轮任务 hook:封装 ask -> openSSE 的发起流程,占位实现
import { useCallback, useRef } from 'react';
import { ask } from '../api/http';
import { openTaskStream } from '../api/sse';
import { useChat } from '../state/ChatContext';
import type { AgentName, RoundView } from '../state/types';

// 创建一个空 round,4 个 think 都是 pending
function createEmptyRound(taskId: string, userMessage: string): RoundView {
  const emptyAgent = (agent: AgentName) => ({ agent, state: 'pending' as const });
  return {
    taskId,
    state: 'PENDING',
    userMessage,
    thinks: {
      DeepSeek: emptyAgent('DeepSeek'),
      GLM: emptyAgent('GLM'),
      Kimi: emptyAgent('Kimi'),
      Qwen: emptyAgent('Qwen'),
    },
  };
}

export function useChatTask() {
  const { state, dispatch } = useChat();
  // 用 ref 持有 AbortController,便于外部停止 SSE
  const abortRef = useRef<AbortController | null>(null);

  // 发问入口
  const send = useCallback(
    async (message: string): Promise<void> => {
      const trimmed = message.trim();
      if (!trimmed) return;

      // 1. 调 /ask 拿到 taskId
      const { taskId } = await ask({ sessionId: state.sessionId, message: trimmed });

      // 2. 把空 round 落到 state,推到时间线
      dispatch({ type: 'task.created', taskId, userMessage: trimmed });
      dispatch({ type: 'round.append', round: createEmptyRound(taskId, trimmed) });

      // 3. 打开 SSE 流(失败由 sse.ts 内部处理)
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      await openTaskStream(taskId, dispatch, { signal: ctrl.signal });
    },
    [dispatch, state.sessionId],
  );

  // 主动关闭当前流(取消按钮场景)
  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  return { send, stop };
}

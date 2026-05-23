// SSE 客户端:转发到 SharedWorker 通道(api/sseChannel)
// 老接口保留:dispatch 收事件、signal 主动断开、onFatal 致命错通知
//
// 关键差异:
//   - SharedWorker 让 SSE 连接活在 worker 进程 主线程刷新不会断流
//   - 主线程一侧只是 port 收消息 转 dispatch 不再直接持有 fetch
//   - signal abort 仅取消 port 订阅 worker 的底层连接保留 让其它 tab / 重连复用
import type { Dispatch } from 'react';
import type { ChatAction, SSEEvent } from '../state/types';
import { subscribeTask } from './sseChannel';

class FatalSSEError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FatalSSEError';
  }
}

export interface OpenTaskStreamOptions {
  signal?: AbortSignal;
  onError?: (err: unknown) => void;
  onFatal?: (err: unknown) => void;
}

// 打开一条任务的事件流 兼容旧签名 返回 Promise 在订阅结束后 resolve
export function openTaskStream(
  taskId: string,
  dispatch: Dispatch<ChatAction>,
  options?: OpenTaskStreamOptions,
): Promise<void> {
  return new Promise<void>((resolve) => {
    let closed = false;
    const sub = subscribeTask(taskId, {
      onEvent: (type, data, sourceTaskId) => {
        const evt: SSEEvent = { type, data };
        dispatch({ type: 'sse.event', taskId: sourceTaskId, event: evt });
      },
      onStatus: (status) => {
        // 复用现有 sse.status action
        dispatch({ type: 'sse.status', status });
        // worker 关闭时 promise 完结 与老语义对齐
        if (status === 'closed' && !closed) {
          closed = true;
          sub.close();
          resolve();
        }
      },
      onFatal: (reason) => {
        const err = new FatalSSEError(reason);
        options?.onFatal?.(err);
        if (!closed) {
          closed = true;
          sub.close();
          resolve();
        }
      },
    });
    // 外部主动断开
    if (options?.signal) {
      const sig = options.signal;
      const onAbort = (): void => {
        if (!closed) {
          closed = true;
          sub.close();
          resolve();
        }
      };
      if (sig.aborted) onAbort();
      else sig.addEventListener('abort', onAbort, { once: true });
    }
  });
}

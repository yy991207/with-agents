// SSE 客户端:用 @microsoft/fetch-event-source 封装,断网会自动重连
// 404 等致命错误时不重连,直接由调用方感知
import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { Dispatch } from 'react';
import type { ChatAction, SSEEvent } from '../state/types';

// 标记一类 SSE 致命错误:不再重连,直接抛给调用方
class FatalSSEError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FatalSSEError';
  }
}

// 打开一条任务的事件流
// - taskId:后端任务 ID
// - dispatch:全局 reducer dispatch,用于把事件派发到状态
// - signal:外部 AbortController,用于主动断流
export interface OpenTaskStreamOptions {
  signal?: AbortSignal;
  onError?: (err: unknown) => void;
  onFatal?: (err: unknown) => void;
}

export function openTaskStream(
  taskId: string,
  dispatch: Dispatch<ChatAction>,
  options?: OpenTaskStreamOptions,
): Promise<void> {
  // 注意:fetchEventSource 返回 Promise,只在连接结束/出错后 resolve
  return fetchEventSource(`/sse/${encodeURIComponent(taskId)}`, {
    method: 'GET',
    signal: options?.signal,

    // 连接打开
    onopen: async (response) => {
      // 404 / 410 视为致命:任务不存在或已清理,不要重连
      if (response.status === 404 || response.status === 410) {
        throw new FatalSSEError(`任务不存在或已清理:${response.status}`);
      }
      if (
        response.ok &&
        response.headers.get('content-type')?.includes('text/event-stream')
      ) {
        dispatch({ type: 'sse.status', status: 'open' });
        return;
      }
      // 其它非 2xx 也视为致命
      throw new FatalSSEError(`SSE 打开失败:${response.status}`);
    },

    // 收到一条 SSE 消息
    onmessage: (msg) => {
      // 后端约定:event 字段是事件名,data 是 JSON 字符串
      let parsed: Record<string, unknown> = {};
      try {
        parsed = msg.data ? (JSON.parse(msg.data) as Record<string, unknown>) : {};
      } catch {
        parsed = { raw: msg.data };
      }
      const evt: SSEEvent = {
        type: msg.event || 'message',
        data: parsed,
      };
      dispatch({ type: 'sse.event', event: evt });
    },

    // 连接关闭(后端正常 FIN)
    onclose: () => {
      dispatch({ type: 'sse.status', status: 'closed' });
    },

    // 出错:返回数字代表延迟多久后重试,抛错代表彻底失败
    onerror: (err) => {
      // 致命错误直接抛出去,fetchEventSource 不会再重试
      if (err instanceof FatalSSEError) {
        dispatch({ type: 'sse.status', status: 'closed' });
        options?.onFatal?.(err);
        throw err;
      }
      dispatch({ type: 'sse.status', status: 'reconnecting' });
      options?.onError?.(err);
      // 简单退避策略:1.5s 重连一次
      return 1500;
    },

    // 关闭页面可见性时不要让浏览器随便断
    openWhenHidden: true,
  });
}

// 主线程一侧:连接 SharedWorker 把它当成 SSE 中枢
// 对调用方暴露和老 openTaskStream 类似的接口 内部通过 worker 转发事件
//
// fallback 策略:
//   - 浏览器不支持 SharedWorker 时 自动回退到主线程 fetch-event-source
//   - 主进程刷新仍然 worker 持续接收 / 单 tab 刷新时 worker 重启 但启动后立刻拉
//     snapshot 比 React mount 快得多 体感几乎无空白

import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { ClientCmd, ServerMsg } from '../workers/sseProtocol';

// 单次订阅暴露给调用方的句柄
export interface SSESubscription {
  // 主动取消订阅 worker 仍保留缓存(便于其他 tab 复用)
  close: () => void;
}

// 订阅回调
export interface SSESubscribeHandlers {
  onEvent: (type: string, data: Record<string, unknown>, taskId: string) => void;
  onStatus?: (status: 'open' | 'reconnecting' | 'closed') => void;
  onFatal?: (reason: string) => void;
}

// 单例的 worker port 与回调注册表
let port: MessagePort | null = null;
// taskId -> handlers
const handlers: Map<string, Set<SSESubscribeHandlers>> = new Map();
// 主线程已收到的最大 seq 用于跨刷新去重 (重新挂回 worker 时 worker 会把缓存全推
// 一遍 这里靠 seq 单调过滤已处理事件)
const lastSeqByTask: Map<string, number> = new Map();

// 是否已尝试过初始化 SharedWorker
let workerInited = false;
// SharedWorker 是否可用 false 时调用方应走 fallback
let workerAvailable = false;

function initSharedWorker(): void {
  if (workerInited) return;
  workerInited = true;
  // 部分浏览器(老 Safari / 隐身模式)无 SharedWorker
  if (typeof SharedWorker === 'undefined') {
    workerAvailable = false;
    return;
  }
  try {
    // Vite 标准写法 ?worker&shared 让 build 出独立 chunk
    // 这里用 import.meta.url 让 dev / prod 路径都对
    const w = new SharedWorker(
      new URL('../workers/sseWorker.ts', import.meta.url),
      { type: 'module', name: 'multichat-sse' },
    );
    port = w.port;
    port.onmessage = (e: MessageEvent<ServerMsg>) => {
      const msg = e.data;
      if (!msg) return;
      if (msg.kind === 'pong') return;
      if (msg.kind === 'event') {
        const last = lastSeqByTask.get(msg.taskId) ?? 0;
        if (msg.seq <= last) return; // 跨刷新去重
        lastSeqByTask.set(msg.taskId, msg.seq);
        const set = handlers.get(msg.taskId);
        if (!set) return;
        for (const h of set) {
          try {
            h.onEvent(msg.type, msg.data, msg.taskId);
          } catch {
            // 单个 handler 异常不影响其它
          }
        }
        return;
      }
      if (msg.kind === 'status') {
        const set = handlers.get(msg.taskId);
        if (!set) return;
        for (const h of set) h.onStatus?.(msg.status);
        return;
      }
      if (msg.kind === 'fatal') {
        const set = handlers.get(msg.taskId);
        if (!set) return;
        for (const h of set) h.onFatal?.(msg.reason);
        return;
      }
    };
    port.start();
    workerAvailable = true;
  } catch {
    workerAvailable = false;
  }
}

function send(cmd: ClientCmd): void {
  if (!port) return;
  try {
    port.postMessage(cmd);
  } catch {
    // ignore
  }
}

// 订阅某 task 的事件流 通过 SharedWorker 拿事件
// 不可用时返回 null 由调用方走 fallback
export function subscribeViaWorker(
  taskId: string,
  hs: SSESubscribeHandlers,
): SSESubscription | null {
  initSharedWorker();
  if (!workerAvailable) return null;

  let set = handlers.get(taskId);
  if (!set) {
    set = new Set();
    handlers.set(taskId, set);
  }
  set.add(hs);
  send({ kind: 'subscribe', taskId });

  return {
    close: () => {
      const cur = handlers.get(taskId);
      if (cur) {
        cur.delete(hs);
        if (cur.size === 0) {
          handlers.delete(taskId);
          send({ kind: 'unsubscribe', taskId });
        }
      }
    },
  };
}

// fallback:走老的主线程 fetch-event-source 方案
export function subscribeFallback(
  taskId: string,
  hs: SSESubscribeHandlers,
): SSESubscription {
  const ctrl = new AbortController();
  void fetchEventSource(`/sse/${encodeURIComponent(taskId)}`, {
    method: 'GET',
    signal: ctrl.signal,
    onopen: async (response) => {
      if (response.status === 404 || response.status === 410) {
        hs.onFatal?.(`任务在服务端不可恢复 (${response.status})`);
        throw new Error('fatal');
      }
      if (
        response.ok &&
        response.headers.get('content-type')?.includes('text/event-stream')
      ) {
        hs.onStatus?.('open');
        return;
      }
      throw new Error(`SSE 打开失败 ${response.status}`);
    },
    onmessage: (msg) => {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = msg.data ? (JSON.parse(msg.data) as Record<string, unknown>) : {};
      } catch {
        parsed = { raw: msg.data };
      }
      hs.onEvent(msg.event || 'message', parsed, taskId);
    },
    onclose: () => {
      hs.onStatus?.('closed');
    },
    onerror: (err) => {
      hs.onStatus?.('reconnecting');
      const msg = err instanceof Error ? err.message : String(err);
      if (msg === 'fatal') throw err;
      return 1500;
    },
    openWhenHidden: true,
  });
  return { close: () => ctrl.abort() };
}

// 入口 优先 worker 不行回退
export function subscribeTask(
  taskId: string,
  hs: SSESubscribeHandlers,
): SSESubscription {
  const sub = subscribeViaWorker(taskId, hs);
  if (sub) return sub;
  return subscribeFallback(taskId, hs);
}

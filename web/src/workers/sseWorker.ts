// SharedWorker:把所有 task 的 SSE 连接托管在 worker 进程
// 主线程刷新或多 tab 都不会断流 worker 在同源最后一个 port 关闭后才退出
//
// 设计要点:
//   1. 每个 taskId 一条独立 SSE 连接 一份 ring buffer 缓存
//   2. 多个 port 可同时订阅同一个 taskId 由 broadcast 自然同步
//   3. fetch + ReadableStream 手写 SSE 解析 SharedWorker 不能用 EventSource API
//   4. 网络异常自动指数退避重连 重连后从头再拿 snapshot 由 seq 单调递增防重复渲染
//      (后端 hub.subscribe 第一帧总会推 snapshot 包含全部历史)
//   5. LRU 上限 20 个 task 防内存涨爆 老 task 缓存按最旧丢
//
// 注意 lib 仅 WebWorker 不要 import 任何 DOM 内容
/// <reference lib="webworker" />

import type { ClientCmd, ServerMsg } from './sseProtocol';

// SharedWorker 全局上下文
// 类型断言到 SharedWorkerGlobalScope 拿到 onconnect 入口
const ctx = self as unknown as SharedWorkerGlobalScope;

// 单个 task 的运行时记录
interface TaskRuntime {
  taskId: string;
  // 已经收到的事件 包含序号 用于回放给后到的 port
  events: ServerMsg[];
  // 最近一次状态 用于新 port 入会时的状态同步
  lastStatus: 'open' | 'reconnecting' | 'closed';
  // 单调递增序号 跨重连保持
  nextSeq: number;
  // 当前正在订阅此 task 的 port 集合
  ports: Set<MessagePort>;
  // SSE 连接控制器 重连或释放时 abort
  abortCtrl: AbortController | null;
  // 重连退避计时器
  retryTimer: number | null;
  // 退避当前等待秒数 指数增长 上限 30s
  retryDelayMs: number;
  // 后端是否已经发过终态 (DONE/CANCELLED) 之后 SSE 自然 close 无需重连
  terminalReached: boolean;
}

// 所有 task 的运行时索引 LRU 顺序由 Map 的插入顺序保证
const tasks: Map<string, TaskRuntime> = new Map();

// LRU 上限
const MAX_TASKS = 20;

// 获取或创建 runtime LRU 命中时把 key 重新放到末尾
function touch(taskId: string): TaskRuntime {
  let rt = tasks.get(taskId);
  if (rt) {
    // 重新放到末尾保持 LRU 顺序
    tasks.delete(taskId);
    tasks.set(taskId, rt);
    return rt;
  }
  rt = {
    taskId,
    events: [],
    lastStatus: 'closed',
    nextSeq: 1,
    ports: new Set(),
    abortCtrl: null,
    retryTimer: null,
    retryDelayMs: 500,
    terminalReached: false,
  };
  tasks.set(taskId, rt);
  // 超出上限淘汰最旧 一个个删 删之前彻底释放底层 SSE 连接
  while (tasks.size > MAX_TASKS) {
    const oldestKey = tasks.keys().next().value;
    if (!oldestKey) break;
    const old = tasks.get(oldestKey);
    if (old) releaseTask(old);
  }
  return rt;
}

// 彻底释放某个 task 的资源 中断 SSE 清缓存
function releaseTask(rt: TaskRuntime): void {
  if (rt.abortCtrl) {
    try {
      rt.abortCtrl.abort();
    } catch {
      // abort 可能因连接已关闭抛出 忽略
    }
    rt.abortCtrl = null;
  }
  if (rt.retryTimer !== null) {
    clearTimeout(rt.retryTimer);
    rt.retryTimer = null;
  }
  rt.ports.clear();
  tasks.delete(rt.taskId);
}

// 向某 task 的所有订阅 port 广播一条消息
function broadcast(rt: TaskRuntime, msg: ServerMsg): void {
  for (const p of rt.ports) {
    try {
      p.postMessage(msg);
    } catch {
      // port 可能已断开 忽略 真正清理由 unsubscribe / 心跳兜底
    }
  }
}

// 推送一条事件:写入缓存 + 广播
function pushEvent(
  rt: TaskRuntime,
  type: string,
  data: Record<string, unknown>,
): void {
  const evt: ServerMsg = {
    kind: 'event',
    taskId: rt.taskId,
    seq: rt.nextSeq++,
    type,
    data,
  };
  rt.events.push(evt);
  // 终态识别 让重连逻辑不要傻乎乎再连
  // CANCELLED 后用户可能重答 需要重置 terminalReached 让 SSE 能继续接收重答事件
  // 当收到 task.state REPLYING 事件时 重置标记 表示新一轮回答正在进行
  if (
    type === 'task.state' &&
    typeof data.state === 'string'
  ) {
    if (data.state === 'REPLYING') {
      // 重答触发 round.state 切回 REPLYING  重置终态标记 允许后续事件流通
      rt.terminalReached = false;
    } else if (data.state === 'DONE' || data.state === 'CANCELLED') {
      rt.terminalReached = true;
    }
  }
  if (type === 'task.unrecoverable') {
    rt.terminalReached = true;
  }
  broadcast(rt, evt);
}

// 推一条状态变化 缓存最后一条便于新 port 入会时同步
function pushStatus(
  rt: TaskRuntime,
  status: 'open' | 'reconnecting' | 'closed',
): void {
  rt.lastStatus = status;
  broadcast(rt, { kind: 'status', taskId: rt.taskId, status });
}

// 推致命错误
function pushFatal(rt: TaskRuntime, reason: string): void {
  rt.terminalReached = true;
  broadcast(rt, { kind: 'fatal', taskId: rt.taskId, reason });
}

// 启动一次 SSE 连接 失败自动指数退避重连
async function startSSE(rt: TaskRuntime): Promise<void> {
  if (rt.terminalReached) return;
  if (rt.abortCtrl) return; // 已经在跑 不重复开

  const ctrl = new AbortController();
  rt.abortCtrl = ctrl;
  pushStatus(rt, 'reconnecting');

  try {
    // SharedWorker 的 fetch 与主线程同源 同 cookies
    const url = `/sse/${encodeURIComponent(rt.taskId)}`;
    const resp = await fetch(url, {
      method: 'GET',
      signal: ctrl.signal,
      headers: { Accept: 'text/event-stream' },
      credentials: 'same-origin',
    });

    // 致命状态 直接终止不重连
    if (resp.status === 404 || resp.status === 410) {
      pushFatal(rt, `任务在服务端不可恢复 (${resp.status})`);
      pushStatus(rt, 'closed');
      rt.abortCtrl = null;
      return;
    }
    if (!resp.ok || !resp.body) {
      // 非 2xx 视为可重试错误
      throw new Error(`SSE 打开失败 ${resp.status}`);
    }

    const ct = resp.headers.get('content-type') || '';
    if (!ct.includes('text/event-stream')) {
      throw new Error(`SSE content-type 不正确: ${ct}`);
    }

    pushStatus(rt, 'open');
    // 连接成功 退避重置
    rt.retryDelayMs = 500;

    // 解析 SSE 流
    await parseSSEStream(rt, resp.body);

    // 流自然结束 (后端 close hub) 走 closed
    pushStatus(rt, 'closed');
    rt.abortCtrl = null;

    // 如果后端没发终态就关流 视为可能仍在跑 尝试重连
    // (终态已发的话 terminalReached 守住不重连)
    if (!rt.terminalReached) {
      scheduleRetry(rt);
    }
  } catch (err) {
    rt.abortCtrl = null;
    // 主动 abort 引起的不当作错误处理
    if (
      err instanceof DOMException &&
      err.name === 'AbortError'
    ) {
      pushStatus(rt, 'closed');
      return;
    }
    pushStatus(rt, 'reconnecting');
    if (!rt.terminalReached) {
      scheduleRetry(rt);
    }
  }
}

// SSE 行解析器 按 EventSource 协议拆 event:/data: 行 空行分隔事件
async function parseSSEStream(
  rt: TaskRuntime,
  body: ReadableStream<Uint8Array>,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let curEvent = 'message';
  let curData: string[] = [];

  // 调度处理一行
  const handleLine = (line: string): void => {
    if (line === '') {
      // 空行 = 一条事件结束
      if (curData.length > 0) {
        const dataStr = curData.join('\n');
        let parsed: Record<string, unknown> = {};
        try {
          parsed = dataStr ? (JSON.parse(dataStr) as Record<string, unknown>) : {};
        } catch {
          parsed = { raw: dataStr };
        }
        pushEvent(rt, curEvent, parsed);
      }
      curEvent = 'message';
      curData = [];
      return;
    }
    // 注释行 sse-starlette 心跳是 ": ping" 直接跳过
    if (line.startsWith(':')) return;
    const idx = line.indexOf(':');
    if (idx < 0) {
      // 无 field 名 整行当 field name 标准要求 一般不出现
      return;
    }
    const field = line.slice(0, idx);
    // SSE 规范: 字段名后有可选 ' '  data:foo / data: foo 都成立
    let value = line.slice(idx + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') {
      curEvent = value;
    } else if (field === 'data') {
      curData.push(value);
    }
    // id / retry 暂不处理
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 按 \n 拆行 SSE 既允许 \n 也允许 \r\n 这里统一处理
    let newlineIdx: number;
    while ((newlineIdx = buffer.indexOf('\n')) >= 0) {
      let line = buffer.slice(0, newlineIdx);
      buffer = buffer.slice(newlineIdx + 1);
      if (line.endsWith('\r')) line = line.slice(0, -1);
      handleLine(line);
    }
  }
  // 末尾残余按一行处理
  if (buffer.length > 0) {
    handleLine(buffer);
    handleLine('');
  }
}

// 安排一次重连 指数退避 上限 30s
function scheduleRetry(rt: TaskRuntime): void {
  if (rt.retryTimer !== null) return;
  if (rt.terminalReached) return;
  if (rt.ports.size === 0) {
    // 没人订阅 不重连 等下次 subscribe 再启动
    return;
  }
  const delay = rt.retryDelayMs;
  rt.retryDelayMs = Math.min(rt.retryDelayMs * 2, 30000);
  rt.retryTimer = (setTimeout(() => {
    rt.retryTimer = null;
    void startSSE(rt);
  }, delay) as unknown) as number;
}

// 给某 port 回放该 task 已缓存事件 + 当前状态
// 顺序: snapshot 不会单独再发 直接把缓存里所有 event 顺序推过去 + 最新 status
function replay(rt: TaskRuntime, port: MessagePort): void {
  for (const e of rt.events) {
    try {
      port.postMessage(e);
    } catch {
      return;
    }
  }
  // 全新 runtime 的 lastStatus 默认是 closed,但这不是一次真实关闭。
  // 如果这里回放给主线程,openTaskStream 会以为流已结束并立刻退订,
  // 后续 reconnecting/open/event 就没人接了。
  if (rt.lastStatus === 'closed' && !rt.terminalReached) return;
  try {
    port.postMessage({
      kind: 'status',
      taskId: rt.taskId,
      status: rt.lastStatus,
    } satisfies ServerMsg);
  } catch {
    // ignore
  }
}

// 处理一条主线程命令
function handleCmd(port: MessagePort, cmd: ClientCmd): void {
  if (cmd.kind === 'ping') {
    try {
      port.postMessage({ kind: 'pong' } satisfies ServerMsg);
    } catch {
      // ignore
    }
    return;
  }
  if (cmd.kind === 'subscribe') {
    const rt = touch(cmd.taskId);
    rt.ports.add(port);
    // 回放缓存
    replay(rt, port);
    // 没有连接就开 已经在跑就让它继续
    if (!rt.abortCtrl && !rt.terminalReached) {
      void startSSE(rt);
    }
    return;
  }
  if (cmd.kind === 'unsubscribe') {
    const rt = tasks.get(cmd.taskId);
    if (rt) rt.ports.delete(port);
    return;
  }
  if (cmd.kind === 'release') {
    const rt = tasks.get(cmd.taskId);
    if (rt) releaseTask(rt);
    return;
  }
}

// SharedWorker 入口 每个新 tab 触发一次 connect
ctx.onconnect = (ev: MessageEvent) => {
  const port = ev.ports[0];
  if (!port) return;
  port.onmessage = (e: MessageEvent<ClientCmd>) => {
    try {
      handleCmd(port, e.data);
    } catch {
      // 单条命令异常不影响其它 port
    }
  };
  // SharedWorker 没有标准的 port disconnect 事件 这里只能等心跳超时再清
  // 用一个轻量的兜底:port 在被 GC 时 postMessage 会抛 detachPort 时机交给 unsubscribe
  port.start();
};

// 暴露空导出 让 TS 把它当模块
export {};

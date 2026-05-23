// SharedWorker 与主线程之间的协议
// 主线程和 worker 都 import 这一份 保证两端 wire 字段同步

// 主线程发给 worker 的命令类型
export type ClientCmd =
  // 订阅某个 task 的事件流 worker 收到立刻把缓存历史回放给 port 然后挂上后续广播
  | { kind: 'subscribe'; taskId: string }
  // 取消订阅 仅当某 port 不再关心此 task 时调用
  // worker 不会因此关闭底层 SSE 连接 因为可能还有其它 tab 在订阅同一个 task
  | { kind: 'unsubscribe'; taskId: string }
  // 主线程主动告知 worker 完全释放某个 task 的缓存与连接 一般不用
  | { kind: 'release'; taskId: string }
  // 心跳:port 仍在 不要被淘汰
  | { kind: 'ping' };

// worker 发给主线程的消息类型
export type ServerMsg =
  // 单条 SSE 事件
  | {
      kind: 'event';
      taskId: string;
      // 事件序号 单调递增 主线程拿来去重以及做幂等回放
      seq: number;
      type: string;
      data: Record<string, unknown>;
    }
  // SSE 连接状态变化
  | {
      kind: 'status';
      taskId: string;
      status: 'open' | 'reconnecting' | 'closed';
    }
  // SSE 致命错误 (404/410) port 应该按 fatal 处理
  | {
      kind: 'fatal';
      taskId: string;
      reason: string;
    }
  // 心跳响应
  | { kind: 'pong' };

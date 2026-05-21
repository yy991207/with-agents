// localStorage 持久化封装:仅存 sessionId 和 activeTaskId,不存 chat 内容
// 抗刷新断线重连时,App 在启动阶段读这两个 key 决定是否拉历史 + 重连 SSE
// 注意:存取动作都包 try-catch,避免 Safari 隐身模式下 localStorage 抛错把整个应用拖崩

const SESSION_KEY = 'multichat.sessionId';
const TASK_KEY = 'multichat.activeTaskId';

export interface PersistedState {
  sessionId: string | null;
  activeTaskId: string | null;
}

// 读取持久化的 sessionId 和 activeTaskId
export function loadPersisted(): PersistedState {
  try {
    return {
      sessionId: localStorage.getItem(SESSION_KEY),
      activeTaskId: localStorage.getItem(TASK_KEY),
    };
  } catch {
    return { sessionId: null, activeTaskId: null };
  }
}

// 写入或清空 sessionId,null 时直接 remove
export function persistSession(sessionId: string | null): void {
  try {
    if (sessionId) localStorage.setItem(SESSION_KEY, sessionId);
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* 隐身模式下 localStorage 不可写,忽略 */
  }
}

// 写入或清空 activeTaskId,null 时直接 remove
export function persistActiveTask(taskId: string | null): void {
  try {
    if (taskId) localStorage.setItem(TASK_KEY, taskId);
    else localStorage.removeItem(TASK_KEY);
  } catch {
    /* 隐身模式下 localStorage 不可写,忽略 */
  }
}

// 一次性把两个 key 都清掉
export function clearPersisted(): void {
  try {
    localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem(TASK_KEY);
  } catch {
    /* ignore */
  }
}

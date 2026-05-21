// Chat 全局 Context:基于 useReducer + Context 实现,无第三方状态库
// H1 抗刷新扩展:
//   1. 提供 SSE AbortController 跨组件共享(send / stop / 抗刷新重连共用一份 ref)
//   2. 监听 sessionId / activeTaskId 变化同步到 localStorage
import { createContext, useContext, useEffect, useMemo, useReducer, useRef } from 'react';
import type { Dispatch, ReactNode } from 'react';
import { chatReducer, initialState } from './reducer';
import type { ChatAction, ChatState } from './types';
import { persistActiveTask, persistSession } from './persistence';

// Context 暴露的接口
interface ChatContextValue {
  state: ChatState;
  dispatch: Dispatch<ChatAction>;
  // 注册当前活跃 SSE 的 AbortController:任何模块新建 SSE 都应调用此方法登记
  // 旧的会先 abort 再被替换,保证同一时刻只存在一条 SSE
  registerSSEController: (ctrl: AbortController | null) => void;
  // 主动断开当前 SSE:供 stop / session.switch / 抗刷新失败时调用
  closeSSEController: () => void;
}

// 创建 Context,默认值为 null,使用方必须包裹 Provider
const ChatContext = createContext<ChatContextValue | null>(null);

// Provider 组件
export function ChatProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(chatReducer, initialState);

  // SSE controller ref:跨 hook / 组件共享,避免遗漏 abort 导致僵尸连接
  const sseRef = useRef<AbortController | null>(null);

  // 注册新 controller:把旧的 abort 掉,再挂上新引用
  // 用 useRef + 普通函数即可,不需要 useCallback(引用稳定)
  const registerSSEController = (ctrl: AbortController | null): void => {
    if (sseRef.current && sseRef.current !== ctrl) {
      try {
        sseRef.current.abort();
      } catch {
        /* abort 出错不影响主流程 */
      }
    }
    sseRef.current = ctrl;
  };

  const closeSSEController = (): void => {
    if (sseRef.current) {
      try {
        sseRef.current.abort();
      } catch {
        /* ignore */
      }
      sseRef.current = null;
    }
  };

  // ---- localStorage 持久化副作用 ----
  // 只持久化 sessionId,session.switch / task.created 都会触发
  useEffect(() => {
    persistSession(state.sessionId);
  }, [state.sessionId]);

  // 只持久化"未结束"的 activeTaskId,DONE / CANCELLED 后清掉,避免下次刷新无意义重连
  useEffect(() => {
    const finished = state.taskState === 'DONE' || state.taskState === 'CANCELLED';
    persistActiveTask(finished ? null : state.activeTaskId);
  }, [state.activeTaskId, state.taskState]);

  // 用 useMemo 避免无意义的子组件重渲染
  // 注意:registerSSEController / closeSSEController 是闭包,但内部读 ref,引用变化不影响行为
  const value = useMemo<ChatContextValue>(
    () => ({ state, dispatch, registerSSEController, closeSSEController }),
    [state],
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

// 自定义 hook:在子组件里读写全局状态
export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error('useChat 必须在 ChatProvider 内部使用');
  }
  return ctx;
}

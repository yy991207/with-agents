// Chat 全局 Context:基于 useReducer + Context 实现,无第三方状态库
import { createContext, useContext, useMemo, useReducer } from 'react';
import type { Dispatch, ReactNode } from 'react';
import { chatReducer, initialState } from './reducer';
import type { ChatAction, ChatState } from './types';

// Context 暴露的接口
interface ChatContextValue {
  state: ChatState;
  dispatch: Dispatch<ChatAction>;
}

// 创建 Context,默认值为 null,使用方必须包裹 Provider
const ChatContext = createContext<ChatContextValue | null>(null);

// Provider 组件
export function ChatProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  // 用 useMemo 避免无意义的子组件重渲染
  const value = useMemo<ChatContextValue>(() => ({ state, dispatch }), [state]);
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

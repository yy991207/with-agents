// 会话 hook:封装会话列表、切换会话与拉历史
// /history 返回 { session, rounds[] },与 M3 后端契约一致
// H1 改造:切换会话时主动关闭旧 SSE,避免旧任务事件继续落到新会话视图
// 修复:后端返回 snake_case dict 缺前端字段(toolCalls 等),需先转换再 dispatch
import { useCallback, useEffect } from 'react';
import { message } from 'antd';
import { getHistory, listSessions } from '../api/http';
import { useChat } from '../state/ChatContext';
import type { AgentName, RoundView, SessionMeta, TaskState, ThinkState, ThinkView } from '../state/types';

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// 把后端 /sessions 返回的 snake_case dict 转成前端 SessionMeta camelCase
function convertSession(raw: unknown): SessionMeta {
  const r = (raw ?? {}) as Record<string, unknown>;
  return {
    sessionId: (r.session_id as string) ?? (r.sessionId as string) ?? '',
    title: (r.title as string) ?? '',
    updatedAt: (r.updated_at as string) ?? (r.updatedAt as string) ?? '',
  };
}

// 把后端 mongo round 文档转成前端 RoundView
// 后端字段为 snake_case 且 reply 不带 toolCalls  state 是小写枚举
// 此函数兼容历史不同版本字段(question / user_message  reply_content 等)
function convertRound(raw: unknown): RoundView {
  const r = (raw ?? {}) as Record<string, unknown>;
  const stateRaw = String(r.state ?? 'done');
  // 后端枚举小写 前端 TaskState 是大写
  const state = stateRaw.toUpperCase() as TaskState;

  // thinks 可能是 dict 或缺失  保证类型安全
  const thinksRaw = (r.thinks as Record<string, unknown> | undefined) ?? {};
  const thinks = {} as Record<AgentName, ThinkView>;
  for (const [agent, t] of Object.entries(thinksRaw)) {
    const tv = (t ?? {}) as Record<string, unknown>;
    (thinks as Record<string, ThinkView>)[agent] = {
      agent: agent as AgentName,
      state: ((tv.state as ThinkState) ?? 'pending'),
      content: tv.content as string | undefined,
      error: tv.error as string | undefined,
    };
  }

  // 兼容字段名 早期 mongo 用 question 与 reply_content
  const userMessage = (r.user_message as string) ?? (r.question as string) ?? '';

  // reply 字段补全 toolCalls(后端不存中间过程 历史一律为空)
  const replyRaw = r.reply as Record<string, unknown> | undefined;
  const reply = replyRaw
    ? {
        agent: (replyRaw.agent as AgentName) ?? '',
        state: (replyRaw.state as 'streaming' | 'done' | 'failed' | 'cancelled') ?? 'done',
        content: (replyRaw.content as string) ?? '',
        toolCalls: [],
        error: replyRaw.error as string | undefined,
      }
    : undefined;

  // decision 直接透传  字段名一致  choice 类型放宽避免 strict 报错
  const decisionRaw = r.decision as Record<string, unknown> | undefined;
  const decision = decisionRaw
    ? ({
        choice: (decisionRaw.choice as string) ?? '',
        reason: (decisionRaw.reason as string) ?? '',
      } as RoundView['decision'])
    : undefined;

  return {
    taskId: (r.task_id as string) ?? (r.taskId as string) ?? '',
    state,
    userMessage,
    thinks,
    decision,
    reply,
  };
}

export function useSession() {
  const { state, dispatch, closeSSEController } = useChat();

  // 拉会话列表
  const refreshSessions = useCallback(async (): Promise<void> => {
    try {
      const list = await listSessions();
      // 后端返回 snake_case  必须转成 SessionMeta 否则 sessionId 是 undefined
      const sessions = (list as unknown as unknown[]).map(convertSession);
      dispatch({ type: 'sessions.set', sessions });
    } catch (e) {
      // 列表拉失败不影响主流程,仅提示
      message.warning(`会话列表加载失败:${describeError(e)}`);
    }
  }, [dispatch]);

  // 切到指定 session 并加载历史
  const switchSession = useCallback(
    async (sessionId: string | null): Promise<void> => {
      // 先把旧 SSE 关掉,防止旧 round 的事件继续往新会话上写
      closeSSEController();
      // 重置当前视图,等 history.loaded 重新填回
      dispatch({ type: 'session.switch', sessionId });
      if (!sessionId) return;
      try {
        const resp = await getHistory(sessionId);
        // 后端返回的是 snake_case dict 不能直接当 RoundView 用
        // 必须经 convertRound 转成前端结构  补齐 toolCalls 等字段
        const rounds = (resp.rounds as unknown as unknown[]).map(convertRound);
        dispatch({ type: 'history.loaded', sessionId, rounds });
      } catch (e) {
        message.error(`加载历史失败:${describeError(e)}`);
      }
    },
    [closeSSEController, dispatch],
  );

  // 首次挂载拉一下会话列表(占位,真实 UI 时机可能不同)
  useEffect(() => {
    refreshSessions().catch(() => {
      // refreshSessions 内部已经 toast 过,这里兜底吞异常
    });
  }, [refreshSessions]);

  return {
    sessions: state.sessions,
    sessionId: state.sessionId,
    refreshSessions,
    switchSession,
  };
}

// 顶层布局:Sider(SessionDrawer) + Content(Timeline + ChatInput) + 配置抽屉入口
// H1 抗刷新启动:首次 mount 读 localStorage,拉历史 + 重连 SSE
// 启动时加载 agent 列表填充 settings.drafts，保证 Timeline 渲染前 agentLabel 已有数据
import { useEffect, useRef } from 'react';
import { message } from 'antd';
import SettingsDrawer from './components/SettingsDrawer';
import Timeline from './components/Timeline';
import ChatInput from './components/ChatInput';
import ReplyFullscreen from './components/ReplyFullscreen';
import LobeChatView from './components/lobehub/LobeChatView';
import LobeHomeView from './components/lobehub/LobeHomeView';
import LobePlaceholderView from './components/lobehub/LobePlaceholderView';
import LobeSidebar from './components/lobehub/LobeSidebar';
import LobeTaskView from './components/lobehub/LobeTaskView';
import LobeWorkbenchShell from './components/lobehub/LobeWorkbenchShell';
import type { RecommendCardDefinition } from './components/lobehub/lobeData';
import { useChatTask, isFatalSSEError } from './hooks/useChatTask';
import { useSettings } from './hooks/useSettings';
import { useChat } from './state/ChatContext';
import { getAgents, getHistory } from './api/http';
import { openTaskStream } from './api/sse';
import { convertAgentView, convertRound } from './state/converters';
import { parseContextUsage } from './state/reducer';
import { findResumableTaskId } from './state/taskResume';
import {
  clearPersisted,
  loadPersisted,
  persistActiveTask,
} from './state/persistence';
import type { WorkbenchView } from './state/types';

export default function App() {
  const { send, stop } = useChatTask();
  const { state, dispatch, registerSSEController } = useChat();
  // 配置抽屉的开关入口由 useSettings 暴露
  const { openDrawer } = useSettings();

  // StrictMode 下 useEffect 会跑两次,用 ref 守住只跑一次的"启动恢复"
  const bootstrappedRef = useRef(false);
  // 滚动容器 ref 用于自动滚到底部
  const scrollRef = useRef<HTMLDivElement>(null);

  // 自动滚到底部: 新 round 追加 / reply 流式增长 / 切换会话时触发
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  };

  // 流式回复时 content 持续变化 每次 rounds 刷新都滚到底
  useEffect(() => {
    scrollToBottom();
  }, [state.rounds]);

  // 切换会话时滚到底（rounds.set 不单独触发 靠 sessionId 变化驱动）
  useEffect(() => {
    scrollToBottom();
  }, [state.sessionId]);

  // 首次 mount:读取 localStorage,尝试恢复 session + 重连 SSE
  useEffect(() => {
    if (bootstrappedRef.current) return;
    bootstrappedRef.current = true;

    void (async () => {
      const { sessionId, activeTaskId } = loadPersisted();
      if (!sessionId) return;

      try {
        const hist = await getHistory(sessionId);
        const rounds = (hist.rounds as unknown as unknown[]).map(convertRound);
        const sessRaw = (hist.session ?? {}) as unknown as Record<string, unknown>;
        const usageRaw = sessRaw['context_usage'];
        const contextUsage =
          usageRaw && typeof usageRaw === 'object'
            ? parseContextUsage(usageRaw as Record<string, unknown>)
            : null;
        dispatch({ type: 'history.loaded', sessionId, rounds, contextUsage });

        try {
          const agentsResp = await getAgents();
          const agents = (agentsResp.agents ?? []).map(convertAgentView);
          dispatch({
            type: 'settings.loaded',
            agents,
            judgeTarget: agentsResp.judge_target,
          });
        } catch {
          // agent 列表加载失败不阻塞页面恢复
        }

        const resumableTaskId = findResumableTaskId(rounds, activeTaskId);
        if (activeTaskId && !resumableTaskId) {
          persistActiveTask(null);
          return;
        }
        if (!resumableTaskId) return;

        dispatch({ type: 'task.resume', taskId: resumableTaskId });

        const ctrl = new AbortController();
        registerSSEController(ctrl);
        void openTaskStream(resumableTaskId, dispatch, {
          signal: ctrl.signal,
          onFatal: (err) => {
            if (isFatalSSEError(err)) {
              dispatch({
                type: 'sse.event',
                taskId: resumableTaskId,
                event: {
                  type: 'task.unrecoverable',
                  data: { reason: '任务在服务端不可恢复' },
                },
              });
              persistActiveTask(null);
            } else {
              const m = err instanceof Error ? err.message : String(err);
              message.error(`SSE 连接失败:${m}`);
            }
          },
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes('404')) {
          clearPersisted();
        } else {
          message.warning(`恢复历史失败:${msg}`);
        }
        return;
      }
    })();
  }, [dispatch, registerSSEController]);

  const handleNavigate = (view: WorkbenchView) => {
    dispatch({ type: 'ui.view.set', view });
  };

  // 推荐卡片  默认走单 agent (judgeTarget 兜底  没设置就第一个 agent)
  const handleRecommendAction = (card: RecommendCardDefinition) => {
    if (card.action === 'send' && card.prompt) {
      const fallbackAgent =
        state.settings.judgeTarget ||
        Object.keys(state.settings.drafts)[0] ||
        '';
      if (!fallbackAgent) {
        message.error('未配置 agent  请先在配置抽屉里新建一个 agent');
        return;
      }
      void send(card.prompt, {
        agents: [fallbackAgent],
        inputMode: 'single',
      });
      return;
    }
    if (card.action === 'settings') {
      void openDrawer();
      return;
    }
    if (card.action === 'view' && card.view) {
      dispatch({ type: 'ui.view.set', view: card.view });
    }
  };

  const inputNode = <ChatInput onSend={send} onStop={stop} />;
  const timelineNode = <Timeline />;

  const renderWorkbenchContent = () => {
    if (state.workbench.activeView === 'home') {
      return (
        <LobeHomeView
          input={inputNode}
          recommendPage={state.workbench.recommendPage}
          onAction={handleRecommendAction}
          onRotateRecommendations={() => dispatch({ type: 'ui.recommend.rotate' })}
        />
      );
    }

    if (state.workbench.activeView === 'chat') {
      return (
        <LobeChatView
          input={inputNode}
          scrollRef={scrollRef}
          timeline={timelineNode}
        />
      );
    }

    if (state.workbench.activeView === 'tasks') {
      return (
        <LobeTaskView
          onOpenChat={() => handleNavigate('chat')}
          onNavigate={handleNavigate}
        />
      );
    }

    return (
      <LobePlaceholderView
        view={state.workbench.activeView}
        onGoHome={() => handleNavigate('home')}
        onOpenChat={() => handleNavigate('chat')}
        onOpenSettings={() => {
          void openDrawer();
        }}
      />
    );
  };

  return (
    <LobeWorkbenchShell
      sidebar={
        <LobeSidebar
          onNavigate={handleNavigate}
          onOpenSettings={() => {
            void openDrawer();
          }}
        />
      }
    >
      {renderWorkbenchContent()}
      <SettingsDrawer />
      <ReplyFullscreen />
    </LobeWorkbenchShell>
  );
}

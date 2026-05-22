// 顶层布局:Sider(SessionDrawer) + Content(Timeline + ChatInput) + 配置抽屉入口
// H1 抗刷新启动:首次 mount 读 localStorage,拉历史 + 重连 SSE
import { useEffect, useRef } from 'react';
import { Button, Layout, Tooltip, message } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import SessionDrawer from './components/SessionDrawer';
import SettingsDrawer from './components/SettingsDrawer';
import Timeline from './components/Timeline';
import ChatInput from './components/ChatInput';
import { useChatTask, isFatalSSEError } from './hooks/useChatTask';
import { useSettings } from './hooks/useSettings';
import { useChat } from './state/ChatContext';
import { getHistory } from './api/http';
import { openTaskStream } from './api/sse';
import { convertRound } from './state/converters';
import {
  clearPersisted,
  loadPersisted,
  persistActiveTask,
} from './state/persistence';
import type { AgentName } from './state/types';

const { Sider, Content, Header } = Layout;

export default function App() {
  const { send, stop, decideChoice, cancelAgent, retryAgent } = useChatTask();
  const { dispatch, registerSSEController } = useChat();
  // 配置抽屉的开关入口由 useSettings 暴露
  const { openDrawer } = useSettings();

  // StrictMode 下 useEffect 会跑两次,用 ref 守住只跑一次的"启动恢复"
  const bootstrappedRef = useRef(false);

  // 首次 mount:读取 localStorage,尝试恢复 session + 重连 SSE
  useEffect(() => {
    if (bootstrappedRef.current) return;
    bootstrappedRef.current = true;

    void (async () => {
      const { sessionId, activeTaskId } = loadPersisted();
      if (!sessionId) return;

      // 1. 拉历史时间线
      try {
        const hist = await getHistory(sessionId);
        // 后端返回 snake_case dict 必须经 convertRound 转成前端 RoundView
        // 否则 ReplyBubble 渲染历史 reply 时 toolCalls.map() 会崩
        const rounds = (hist.rounds as unknown as unknown[]).map(convertRound);
        // 注意:history.loaded 会强制 activeTaskId=null / taskState=DONE
        // 后面如果有 activeTaskId,再用 task.resume 把它挂回去
        dispatch({ type: 'history.loaded', sessionId, rounds });
      } catch (e) {
        // 历史拉失败:可能 session 已被清,清持久化让用户重新开会话
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes('404')) {
          clearPersisted();
        } else {
          message.warning(`恢复历史失败:${msg}`);
        }
        return;
      }

      // 2. 没有未完成 task 就到此为止
      if (!activeTaskId) return;

      // 3. 把 activeTaskId 挂回去并准备好占位 round 接 snapshot
      dispatch({ type: 'task.resume', taskId: activeTaskId });

      // 4. 重连 SSE;controller 走 Context 共享
      const ctrl = new AbortController();
      registerSSEController(ctrl);
      void openTaskStream(activeTaskId, dispatch, {
        signal: ctrl.signal,
        onFatal: (err) => {
          if (isFatalSSEError(err)) {
            // task hub 已释放,标 round 为 cancelled,清掉持久化让用户能新提问
            dispatch({
              type: 'sse.event',
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
    })();
  }, [dispatch, registerSSEController]);

  // Timeline 给的回调签名都是 (taskId, ...) 形式;hook 里已经从 state 拿 activeTaskId
  // 这里不再校验 taskId 是否一致,信任 Timeline 只在活跃轮上触发回调
  const handleChoose = (
    _taskId: string,
    choice: AgentName | 'auto' | 'regenerate',
  ) => {
    void decideChoice(choice);
  };
  const handleRetry = (_taskId: string, agent: AgentName) => {
    void retryAgent(agent);
  };
  const handlePause = (_taskId: string, agent: AgentName) => {
    void cancelAgent(agent);
  };
  const handleCancel = (_taskId: string) => {
    void stop();
  };

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider width={260} theme="light" style={{ borderRight: '1px solid #e5e7eb' }}>
        <SessionDrawer />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: '0 24px',
            borderBottom: '1px solid #e5e7eb',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <strong>Multi-LLM Chat</strong>
          <Tooltip title="配置管理">
            <Button
              type="text"
              icon={<SettingOutlined />}
              onClick={() => {
                void openDrawer();
              }}
            >
              配置
            </Button>
          </Tooltip>
        </Header>
        <Content style={{ overflowY: 'auto' }}>
          <Timeline
            onChoose={handleChoose}
            onRetryThink={handleRetry}
            onPauseThink={handlePause}
            onCancel={handleCancel}
          />
        </Content>
        <ChatInput onSend={send} onStop={stop} />
      </Layout>
      <SettingsDrawer />
    </Layout>
  );
}

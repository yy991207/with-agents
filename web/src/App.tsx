// 顶层布局:Sider(SessionDrawer) + Content(Timeline + ChatInput)
import { Layout } from 'antd';
import SessionDrawer from './components/SessionDrawer';
import Timeline from './components/Timeline';
import ChatInput from './components/ChatInput';
import { useChatTask } from './hooks/useChatTask';
import { cancel, decide, retryThink } from './api/http';
import type { AgentName } from './state/types';

const { Sider, Content, Header } = Layout;

export default function App() {
  const { send, stop } = useChatTask();

  // 决策点击:转发到后端,真实状态会从 SSE 推回
  const handleChoose = async (taskId: string, choice: AgentName | 'auto' | 'regenerate') => {
    await decide({ taskId, choice });
  };
  const handleRetry = async (taskId: string, agent: AgentName) => {
    await retryThink({ taskId, agent });
  };
  const handleCancel = async (taskId: string) => {
    await cancel(taskId);
  };

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider width={260} theme="light" style={{ borderRight: '1px solid #e5e7eb' }}>
        <SessionDrawer />
      </Sider>
      <Layout>
        <Header style={{ padding: '0 24px', borderBottom: '1px solid #e5e7eb' }}>
          <strong>Multi-LLM Chat</strong>
        </Header>
        <Content style={{ overflowY: 'auto' }}>
          <Timeline
            onChoose={handleChoose}
            onRetryThink={handleRetry}
            onCancel={handleCancel}
          />
        </Content>
        <ChatInput onSend={send} onStop={stop} />
      </Layout>
    </Layout>
  );
}

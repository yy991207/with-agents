// 顶层布局:Sider(SessionDrawer) + Content(Timeline + ChatInput) + 配置抽屉入口
import { Button, Layout, Tooltip } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import SessionDrawer from './components/SessionDrawer';
import SettingsDrawer from './components/SettingsDrawer';
import Timeline from './components/Timeline';
import ChatInput from './components/ChatInput';
import { useChatTask } from './hooks/useChatTask';
import { useSettings } from './hooks/useSettings';
import { cancel, decide, retryThink } from './api/http';
import type { AgentName } from './state/types';

const { Sider, Content, Header } = Layout;

export default function App() {
  const { send, stop } = useChatTask();
  // 配置抽屉的开关入口由 useSettings 暴露
  const { openDrawer } = useSettings();

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
            onCancel={handleCancel}
          />
        </Content>
        <ChatInput onSend={send} onStop={stop} />
      </Layout>
      <SettingsDrawer />
    </Layout>
  );
}

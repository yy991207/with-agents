// 顶层布局:Sider(SessionDrawer) + Content(Timeline + ChatInput) + 配置抽屉入口
import { Button, Layout, Tooltip } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import SessionDrawer from './components/SessionDrawer';
import SettingsDrawer from './components/SettingsDrawer';
import Timeline from './components/Timeline';
import ChatInput from './components/ChatInput';
import { useChatTask } from './hooks/useChatTask';
import { useSettings } from './hooks/useSettings';
import type { AgentName } from './state/types';

const { Sider, Content, Header } = Layout;

export default function App() {
  const { send, stop, decideChoice, cancelAgent, retryAgent } = useChatTask();
  // 配置抽屉的开关入口由 useSettings 暴露
  const { openDrawer } = useSettings();

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

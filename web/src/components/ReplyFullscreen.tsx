// 子窗放大全屏  渲染当前 fullscreenReply 指向的 round.replies[agent]
// 关闭时通过 dispatch ui.fullscreen.set null  退出
import { Button, Tooltip } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import { Flexbox } from 'react-layout-kit';
import ReplyBubble from './ReplyBubble';
import TransientScrollbar from './TransientScrollbar';
import { useChat } from '../state/ChatContext';
import { useBranchSession } from '../hooks/useBranchSession';
import { useChatTask } from '../hooks/useChatTask';
import {
  agentAvatarOf,
  agentLabelOf,
  buildAgentLabelMap,
  buildAgentMetaMap,
} from '../state/agentLabels';

export default function ReplyFullscreen() {
  const { state, dispatch } = useChat();
  const { branch } = useBranchSession();
  const { cancelReplyAgent, retryReplyAgent } = useChatTask();
  const fs = state.fullscreenReply;
  if (!fs) return null;
  const round = state.rounds.find((r) => r.taskId === fs.taskId);
  if (!round) return null;
  const reply = round.replies[fs.agent];
  if (!reply) return null;

  const agentLabels = buildAgentLabelMap(state.settings.drafts);
  const agentMetas = buildAgentMetaMap(state.settings.drafts);

  const handleClose = () => {
    dispatch({ type: 'ui.fullscreen.set', fullscreen: null });
  };

  const handleCancel = () => {
    void cancelReplyAgent(fs.taskId, fs.agent);
  };

  const handleRetry = () => {
    void retryReplyAgent(fs.taskId, fs.agent);
  };

  const handleSwitchAgent = (agent: string) => {
    dispatch({ type: 'ui.fullscreen.agent.set', taskId: fs.taskId, agent });
  };

  const handleBranch = async () => {
    await branch({ taskId: fs.taskId, role: 'assistant', agent: fs.agent });
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15, 23, 42, 0.45)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'stretch',
        justifyContent: 'stretch',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          handleClose();
        }
      }}
    >
      <Flexbox
        gap={12}
        paddingBlock={16}
        style={{
          background: '#fff',
          margin: '32px auto',
          maxWidth: 1080,
          width: 'calc(100% - 64px)',
          maxHeight: 'calc(100vh - 64px)',
          borderRadius: 20,
          boxShadow: '0 24px 64px rgba(15, 23, 42, 0.18)',
          padding: '20px 24px',
          overflow: 'hidden',
        }}
      >
        <Flexbox horizontal align="center" gap={8}>
          <span style={{ flex: 1 }} />
          <Tooltip title="关闭">
            <Button
              aria-label="关闭全屏"
              icon={<CloseOutlined />}
              onClick={handleClose}
              shape="circle"
              type="text"
            />
          </Tooltip>
        </Flexbox>
        <TransientScrollbar
          followResetKey={`${fs.taskId}:${fs.agent}:fullscreen`}
          style={{
            background: '#fff',
            borderRadius: 12,
            flex: 1,
            minHeight: 0,
            overflow: 'auto',
            padding: '0 4px',
          }}
        >
          <ReplyBubble
            reply={reply}
            agentLabel={agentLabelOf(agentLabels, reply.agent)}
            avatarUrl={agentAvatarOf(agentMetas, reply.agent)}
            onCancel={handleCancel}
            onRetry={handleRetry}
            onBranch={handleBranch}
            replyOptions={round.agents.map((name) => round.replies[name]).filter(Boolean)}
            agentMetas={agentMetas}
            onSwitchAgent={handleSwitchAgent}
            fullscreen
          />
        </TransientScrollbar>
      </Flexbox>
    </div>
  );
}

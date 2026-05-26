// 时间线:遍历所有 round  渲染用户气泡 + (单/多) agent 回答 grid + 选答 chips
// 多 agent 模式  每行 2 列 grid  最多 4 个子窗
// 单 agent 模式  单卡铺满
import { Button } from 'antd';
import UserBubble from './UserBubble';
import ReplyBubble from './ReplyBubble';
import SelectReplyChips from './SelectReplyChips';
import { useChat } from '../state/ChatContext';
import { useChatTask } from '../hooks/useChatTask';
import {
  agentAvatarOf,
  agentLabelOf,
  buildAgentLabelMap,
  buildAgentMetaMap,
} from '../state/agentLabels';
import type { AgentName, RoundView } from '../state/types';

export default function Timeline() {
  const { state, dispatch } = useChat();
  const { cancelReplyAgent, retryReplyAgent } = useChatTask();
  const agentLabels = buildAgentLabelMap(state.settings.drafts);
  const agentMetas = buildAgentMetaMap(state.settings.drafts);

  const handleFullscreen = (taskId: string, agent: AgentName) => {
    dispatch({ type: 'ui.fullscreen.set', fullscreen: { taskId, agent } });
  };

  if (state.rounds.length === 0) {
    return (
      <div
        style={{
          alignItems: 'center',
          display: 'flex',
          justifyContent: 'center',
          minHeight: '48vh',
          padding: '24px 0',
        }}
      >
        <div
          style={{
            background: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: 24,
            boxShadow: '0 16px 40px rgba(15, 23, 42, 0.06)',
            maxWidth: 560,
            padding: '28px 24px',
            textAlign: 'center',
            width: '100%',
          }}
        >
          <div style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 20, fontWeight: 700, marginBottom: 8 }}>
            从任何想法开始
          </div>
          <div style={{ color: 'rgba(51, 65, 85, 0.72)', fontSize: 14, lineHeight: 1.8, marginBottom: 18 }}>
            当前聊天工作台已经准备好，可以直接发起一轮 1 个或多个 agent 并行回答。
          </div>
          <Button
            shape="round"
            type="primary"
            onClick={() => dispatch({ type: 'ui.view.set', view: 'home' })}
          >
            返回首页
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: '0 0 8px' }}>
      {state.rounds.map((round, idx) => {
        // 锁定:  下一轮已经发起  即本 round 之后还有 round  此时本轮 chips 不可改
        const locked = idx < state.rounds.length - 1;
        return (
          <RoundBlock
            key={round.taskId}
            round={round}
            locked={locked}
            agentLabels={agentLabels}
            agentMetas={agentMetas}
            onFullscreen={handleFullscreen}
            onCancelReply={(agent) => void cancelReplyAgent(round.taskId, agent)}
            onRetryReply={(agent) => void retryReplyAgent(round.taskId, agent)}
          />
        );
      })}
    </div>
  );
}

interface RoundBlockProps {
  round: RoundView;
  locked: boolean;
  agentLabels: ReturnType<typeof buildAgentLabelMap>;
  agentMetas: ReturnType<typeof buildAgentMetaMap>;
  onFullscreen: (taskId: string, agent: AgentName) => void;
  onCancelReply: (agent: AgentName) => void;
  onRetryReply: (agent: AgentName) => void;
}

function RoundBlock({
  round,
  locked,
  agentLabels,
  agentMetas,
  onFullscreen,
  onCancelReply,
  onRetryReply,
}: RoundBlockProps) {
  const agents = round.agents.length > 0 ? round.agents : Object.keys(round.replies);

  // 单 agent  单卡铺满
  // 多 agent  自动 1~4 个子窗  2 列 grid  超过宽度自动换行
  const isMulti = round.inputMode === 'multi' && agents.length > 1;
  const gridStyle: React.CSSProperties = isMulti
    ? {
        display: 'grid',
        gap: 12,
        gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
      }
    : { display: 'block' };

  return (
    <div style={{ marginBottom: 28 }}>
      <UserBubble content={round.userMessage} createdAt={round.createdAt} />

      <div style={{ marginTop: 12, ...gridStyle }}>
        {agents.map((agent) => {
          const reply = round.replies[agent];
          if (!reply) return null;
          const inProgress = reply.state === 'streaming' || reply.state === 'pending';
          const isSelectedReply = round.selectedReplyAgent === agent;
          // 子窗外框  多 agent 时给一层卡片样式  单 agent 时不加
          // maxHeight 364 = 旧 520 × 0.7  视觉更紧凑  超出走 hover 才显示的滚动条
          // className reply-grid-card 启用 hover 滚动条 (见 styles/global.css)
          const wrapperStyle: React.CSSProperties = isMulti
            ? {
                background: '#fff',
                border: '1px solid rgba(15, 23, 42, 0.06)',
                borderRadius: 16,
                boxShadow: '0 4px 12px rgba(15, 23, 42, 0.04)',
                maxHeight: 364,
                overflow: 'auto',
                padding: '8px 14px',
              }
            : {};
          return (
            <div
              key={`${round.taskId}-${agent}`}
              className={isMulti ? 'reply-grid-card' : undefined}
              style={wrapperStyle}
            >
              <ReplyBubble
                reply={reply}
                agentLabel={agentLabelOf(agentLabels, agent)}
                avatarUrl={agentAvatarOf(agentMetas, agent)}
                onCancel={inProgress ? () => onCancelReply(agent) : undefined}
                onRetry={!inProgress ? () => onRetryReply(agent) : undefined}
                onFullscreen={() => onFullscreen(round.taskId, agent)}
                selected={isMulti && isSelectedReply}
              />
            </div>
          );
        })}
      </div>

      <SelectReplyChips round={round} locked={locked} />
    </div>
  );
}

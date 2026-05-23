// 时间线:遍历所有 round,渲染用户气泡 + think 区域 + 决策 + 回答 + 状态摘要
// 活跃 round = state.activeTaskId 指向且 state ∉ {DONE, CANCELLED}
// 活跃 → 完整布局(ThinkPanel + DecisionCard + ReplyBubble)
// 非活跃 → 折叠态 ThinkCardChip(可点开 Modal 看完整 think)
import { Empty, Tag } from 'antd';
import UserBubble from './UserBubble';
import ThinkPanel from './ThinkPanel';
import ThinkCardChip from './ThinkCardChip';
import DecisionCard from './DecisionCard';
import ReplyBubble from './ReplyBubble';
import { useChat } from '../state/ChatContext';
import { agentLabelOf, buildAgentLabelMap } from '../state/agentLabels';
import type { AgentName, RoundView } from '../state/types';

// 是否当前活跃轮:仍然指向 activeTaskId 且未结束
function isActive(round: RoundView, activeTaskId: string | null): boolean {
  if (round.taskId !== activeTaskId) return false;
  return round.state !== 'DONE' && round.state !== 'CANCELLED';
}

export interface TimelineProps {
  onChoose?: (taskId: string, choice: AgentName | 'auto' | 'regenerate') => void;
  onRetryThink?: (taskId: string, agent: AgentName) => void;
  onPauseThink?: (taskId: string, agent: AgentName) => void;
  onCancel?: (taskId: string) => void;
}

export default function Timeline({
  onChoose,
  onRetryThink,
  onPauseThink,
  onCancel,
}: TimelineProps) {
  const { state } = useChat();
  const agentLabels = buildAgentLabelMap(state.settings.drafts);

  return (
    <div style={{ padding: '16px 24px' }}>
      {state.rounds.length === 0 ? (
        <Empty
          description="开始第一次对话"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ marginTop: 60 }}
        />
      ) : null}

      {state.rounds.map((round) => {
        const active = isActive(round, state.activeTaskId);
        const showThinkPanel =
          active &&
          (round.state === 'PENDING' ||
            round.state === 'THINKING' ||
            round.state === 'THINK_DONE');
        const showDecision = active && round.state === 'THINK_DONE' && onChoose;
        const showReply = round.reply !== undefined;
        const showCancelTag = round.state === 'CANCELLED';

        return (
          <div key={round.taskId} style={{ marginBottom: 24 }}>
            {/* 用户消息 + 已取消标签同行,标签靠右,失败一目了然 */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                gap: 12,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <UserBubble content={round.userMessage} />
              </div>
              {showCancelTag && (
                <Tag color="red" style={{ marginTop: 12, flexShrink: 0 }}>
                  已取消{round.cancelReason ? `:${round.cancelReason}` : ''}
                </Tag>
              )}
            </div>

            {showThinkPanel ? (
              <ThinkPanel
                round={round}
                agentLabels={agentLabels}
                onRetry={
                  onRetryThink
                    ? (agent) => onRetryThink(round.taskId, agent)
                    : undefined
                }
                onPause={
                  onPauseThink
                    ? (agent) => onPauseThink(round.taskId, agent)
                    : undefined
                }
              />
            ) : (
              <ThinkCardChip round={round} agentLabels={agentLabels} />
            )}

            {showDecision && onChoose && (
              <DecisionCard
                agentCandidates={Object.keys(round.thinks)}
                onChoose={(c) => onChoose(round.taskId, c)}
                onCancel={
                  onCancel ? () => onCancel(round.taskId) : undefined
                }
                availableAgents={round.availableAgents}
                judgePick={round.judgePick}
              />
            )}

            {showReply && round.reply && (
              <ReplyBubble
                reply={round.reply}
                agentLabel={agentLabelOf(agentLabels, round.reply.agent)}
                onRetry={
                  onCancel
                    ? () => onCancel(round.taskId)
                    : undefined
                }
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

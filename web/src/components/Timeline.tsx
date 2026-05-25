// 时间线:遍历所有 round,渲染用户气泡 + think 区域 + 决策 + 回答 + 状态摘要
// 活跃 round = state.activeTaskId 指向且 state ∉ {DONE, CANCELLED}
import { Button } from 'antd';
import UserBubble from './UserBubble';
import ThinkPanel from './ThinkPanel';
import DecisionCard from './DecisionCard';
import ReplyBubble from './ReplyBubble';
import { useChat } from '../state/ChatContext';
import {
  agentAvatarOf,
  agentLabelOf,
  buildAgentLabelMap,
  buildAgentMetaMap,
} from '../state/agentLabels';
import type { AgentName, RoundView } from '../state/types';

function isActive(round: RoundView, activeTaskId: string | null): boolean {
  if (round.taskId !== activeTaskId) return false;
  return round.state !== 'DONE' && round.state !== 'CANCELLED';
}

export interface TimelineProps {
  onChoose?: (taskId: string, choice: AgentName | 'auto' | 'regenerate') => void;
  onRetryThink?: (taskId: string, agent: AgentName) => void;
  onCancel?: (taskId: string) => void;
}

export default function Timeline({
  onChoose,
  onRetryThink,
  onCancel,
}: TimelineProps) {
  const { state, dispatch } = useChat();
  const agentLabels = buildAgentLabelMap(state.settings.drafts);
  const agentMetas = buildAgentMetaMap(state.settings.drafts);

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
            当前聊天工作台已经准备好，你可以直接发起一轮多 agent 问答，或者回到首页用推荐卡片快速开始。
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
      {state.rounds.map((round) => {
        const active = isActive(round, state.activeTaskId);
        const showThinkPanel =
          active &&
          (round.state === 'PENDING' ||
            round.state === 'THINKING' ||
            round.state === 'THINK_DONE');
        const showDecision = active && round.state === 'THINK_DONE' && onChoose;
        const showReply = round.reply !== undefined;

        return (
          <div key={round.taskId} style={{ marginBottom: 28 }}>
            <UserBubble
              cancelReason={round.cancelReason}
              cancelled={round.state === 'CANCELLED'}
              content={round.userMessage}
            />

            {showThinkPanel ? (
              <div style={{ marginTop: 10 }}>
                <ThinkPanel
                  round={round}
                  agentLabels={agentLabels}
                  agentMetas={agentMetas}
                  onRetry={
                    onRetryThink
                      ? (agent) => onRetryThink(round.taskId, agent)
                      : undefined
                  }
                />
              </div>
            ) : null}

            {showDecision && onChoose ? (
              <div style={{ marginTop: 10 }}>
                <DecisionCard
                  agentCandidates={Object.keys(round.thinks)}
                  onChoose={(choice) => onChoose(round.taskId, choice)}
                  onCancel={
                    onCancel ? () => onCancel(round.taskId) : undefined
                  }
                  availableAgents={round.availableAgents}
                  judgePick={round.judgePick}
                />
              </div>
            ) : null}

            {showReply && round.reply ? (
              <div style={{ marginTop: 12 }}>
                <ReplyBubble
                  reply={round.reply}
                  agentLabel={agentLabelOf(agentLabels, round.reply.agent)}
                  avatarUrl={agentAvatarOf(agentMetas, round.reply.agent)}
                  onRetry={
                    onCancel
                      ? () => onCancel(round.taskId)
                      : undefined
                  }
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

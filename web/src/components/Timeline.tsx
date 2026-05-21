// 时间线:遍历所有 round,渲染用户气泡 + think 区域 + 决策 + 回答
import UserBubble from './UserBubble';
import ThinkPanel from './ThinkPanel';
import ThinkCardChip from './ThinkCardChip';
import DecisionCard from './DecisionCard';
import ReplyBubble from './ReplyBubble';
import { useChat } from '../state/ChatContext';
import type { AgentName, RoundView } from '../state/types';

// 判断这一轮是不是当前活跃轮(决定 think 是详细态还是 chip 折叠态)
function isActive(round: RoundView, activeTaskId: string | null): boolean {
  return round.taskId === activeTaskId;
}

export interface TimelineProps {
  onChoose?: (taskId: string, choice: AgentName | 'auto' | 'regenerate') => void;
  onRetryThink?: (taskId: string, agent: AgentName) => void;
  onCancel?: (taskId: string) => void;
}

export default function Timeline({ onChoose, onRetryThink, onCancel }: TimelineProps) {
  const { state } = useChat();
  return (
    <div style={{ padding: '16px 24px' }}>
      {state.rounds.length === 0 && (
        <div style={{ color: 'rgba(0,0,0,0.45)', textAlign: 'center', marginTop: 64 }}>
          还没有对话,从下方输入第一个问题吧
        </div>
      )}
      {state.rounds.map((round) => {
        const active = isActive(round, state.activeTaskId);
        return (
          <div key={round.taskId} style={{ marginBottom: 24 }}>
            <UserBubble content={round.userMessage} />
            {active ? (
              <ThinkPanel
                round={round}
                onRetry={onRetryThink ? (agent) => onRetryThink(round.taskId, agent) : undefined}
              />
            ) : (
              <div style={{ margin: '4px 0' }}>
                {(['DeepSeek', 'GLM', 'Kimi', 'Qwen'] as AgentName[]).map((a) => (
                  <ThinkCardChip key={a} think={round.thinks[a]} />
                ))}
              </div>
            )}
            {active && round.state === 'THINK_DONE' && onChoose && (
              <DecisionCard
                onChoose={(c) => onChoose(round.taskId, c)}
                onCancel={onCancel ? () => onCancel(round.taskId) : undefined}
              />
            )}
            {round.reply && <ReplyBubble reply={round.reply} />}
          </div>
        );
      })}
    </div>
  );
}

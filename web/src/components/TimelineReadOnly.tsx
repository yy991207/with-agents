import UserBubble from './UserBubble';
import ReplyBubble from './ReplyBubble';
import type { RoundView } from '../state/types';
import {
  agentAvatarOf,
  agentLabelOf,
  buildAgentLabelMap,
  buildAgentMetaMap,
} from '../state/agentLabels';
import { useChat } from '../state/ChatContext';

export interface TimelineReadOnlyProps {
  rounds: RoundView[];
}

export default function TimelineReadOnly({ rounds }: TimelineReadOnlyProps) {
  const { state } = useChat();
  const agentLabels = buildAgentLabelMap(state.settings.drafts);
  const agentMetas = buildAgentMetaMap(state.settings.drafts);

  return (
    <div style={{ padding: '0 0 8px' }}>
      {rounds.map((round) => {
        const agents = round.agents.length > 0 ? round.agents : Object.keys(round.replies);
        const isMulti = round.inputMode === 'multi' && agents.length > 1;
        const gridStyle: React.CSSProperties = isMulti
          ? {
              display: 'grid',
              gap: 12,
              gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
              marginTop: 12,
            }
          : { display: 'block', marginTop: 12 };
        return (
          <div key={round.taskId} style={{ marginBottom: 28 }}>
            <UserBubble content={round.userMessage} createdAt={round.createdAt} />
            <div style={gridStyle}>
              {agents.map((agent) => {
                const reply = round.replies[agent];
                if (!reply) return null;
                return (
                  <div
                    key={`${round.taskId}-${agent}`}
                    style={
                      isMulti
                        ? {
                            background: '#fff',
                            border: '1px solid rgba(15, 23, 42, 0.06)',
                            borderRadius: 16,
                            boxShadow: '0 4px 12px rgba(15, 23, 42, 0.04)',
                            maxHeight: 364,
                            overflow: 'auto',
                            padding: '8px 14px',
                          }
                        : undefined
                    }
                  >
                    <ReplyBubble
                      reply={reply}
                      agentLabel={agentLabelOf(agentLabels, agent)}
                      avatarUrl={agentAvatarOf(agentMetas, agent)}
                      selected={isMulti && round.selectedReplyAgent === agent}
                      fullscreen={false}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

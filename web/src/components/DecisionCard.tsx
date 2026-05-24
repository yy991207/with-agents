// 决策卡:让用户在动态 agent 列表 / auto / regenerate 之间选择
import { Button, Tag } from 'antd';
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { getAgentColor } from '../theme/tokens';
import { useChat } from '../state/ChatContext';
import type { AgentName } from '../state/types';

export interface DecisionCardProps {
  agentCandidates: AgentName[];
  onChoose: (choice: AgentName | 'auto' | 'regenerate') => void;
  onCancel?: () => void;
  availableAgents?: AgentName[];
  judgePick?: AgentName;
  disabled?: boolean;
}

export default function DecisionCard({
  agentCandidates,
  onChoose,
  onCancel: _onCancel,
  availableAgents,
  judgePick,
  disabled,
}: DecisionCardProps) {
  const { state } = useChat();

  const labelOf = (name: string): string => {
    const draft = state.settings.drafts[name];
    return draft?.displayName || name;
  };

  const allow = (agent: AgentName): boolean =>
    !availableAgents || availableAgents.includes(agent);

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 18,
        boxShadow: '0 10px 24px rgba(15, 23, 42, 0.05)',
        padding: 16,
      }}
    >
      <div style={{ alignItems: 'center', display: 'flex', gap: 8, justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <div style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
            选一个 agent 来回答
          </div>
          <div style={{ color: 'rgba(51, 65, 85, 0.68)', fontSize: 13 }}>
            当前已经完成思考，你可以手动选择回答者，或者交给系统帮你决定。
          </div>
        </div>
        {judgePick ? (
          <Tag color={getAgentColor(judgePick)} style={{ borderRadius: 999, margin: 0 }}>
            判官推荐 {labelOf(judgePick)}
          </Tag>
        ) : null}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {agentCandidates.map((agent) => {
          const enabled = allow(agent);
          const color = getAgentColor(agent);
          return (
            <Button
              key={agent}
              disabled={disabled || !enabled}
              onClick={() => onChoose(agent)}
              shape="round"
              style={{
                borderColor: enabled ? color : undefined,
                color: enabled ? color : undefined,
              }}
            >
              选 {labelOf(agent)}
            </Button>
          );
        })}
        <Button
          icon={<ThunderboltOutlined />}
          disabled={disabled}
          onClick={() => onChoose('auto')}
          shape="round"
        >
          帮我选
        </Button>
        <Button
          icon={<ReloadOutlined />}
          disabled={disabled}
          onClick={() => onChoose('regenerate')}
          shape="round"
        >
          重新思考
        </Button>
      </div>
    </div>
  );
}

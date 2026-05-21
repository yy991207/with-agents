// 决策卡:让用户在 4 个 agent / auto / regenerate 之间选择
import { Button, Card, Space } from 'antd';
import { agentColors } from '../theme/tokens';
import type { AgentName } from '../state/types';

export interface DecisionCardProps {
  onChoose: (choice: AgentName | 'auto' | 'regenerate') => void;
  onCancel?: () => void;
  disabled?: boolean;
}

const AGENTS: AgentName[] = ['DeepSeek', 'GLM', 'Kimi', 'Qwen'];

export default function DecisionCard({ onChoose, onCancel, disabled }: DecisionCardProps) {
  return (
    <Card size="small" title="选择一个回答">
      <Space wrap>
        {AGENTS.map((agent) => (
          <Button
            key={agent}
            disabled={disabled}
            onClick={() => onChoose(agent)}
            style={{ borderColor: agentColors[agent], color: agentColors[agent] }}
          >
            选 {agent}
          </Button>
        ))}
        <Button disabled={disabled} onClick={() => onChoose('auto')}>
          帮我选
        </Button>
        <Button disabled={disabled} onClick={() => onChoose('regenerate')}>
          重新 think
        </Button>
        {onCancel && (
          <Button danger disabled={disabled} onClick={onCancel}>
            取消
          </Button>
        )}
      </Space>
    </Card>
  );
}

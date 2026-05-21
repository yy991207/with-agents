// 决策卡:让用户在 4 个 agent / auto / regenerate 之间选择
// availableAgents 表示哪些 agent 的 think 成功 可被选;失败/取消的会灰着不可点
import { Button, Card, Space, Tag } from 'antd';
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { agentColors } from '../theme/tokens';
import type { AgentName } from '../state/types';
import { KNOWN_AGENTS } from '../state/types';

export interface DecisionCardProps {
  onChoose: (choice: AgentName | 'auto' | 'regenerate') => void;
  onCancel?: () => void;
  // 可选:仅这些 agent 可点击;为空/未传则全部可点
  availableAgents?: AgentName[];
  // 判官推荐(judge.done 后端给的)
  judgePick?: AgentName;
  disabled?: boolean;
}

export default function DecisionCard({
  onChoose,
  onCancel,
  availableAgents,
  judgePick,
  disabled,
}: DecisionCardProps) {
  // 没传 availableAgents 视为全部可点,M2 后端会推过来
  const allow = (a: AgentName): boolean =>
    !availableAgents || availableAgents.includes(a);

  return (
    <Card
      size="small"
      title="选一个 agent 来回答"
      style={{ margin: '8px 0' }}
      extra={
        judgePick ? (
          <Tag color={agentColors[judgePick]}>判官推荐 {judgePick}</Tag>
        ) : null
      }
    >
      <Space wrap>
        {KNOWN_AGENTS.map((agent) => {
          const ok = allow(agent);
          return (
            <Button
              key={agent}
              disabled={disabled || !ok}
              onClick={() => onChoose(agent)}
              style={{
                borderColor: ok ? agentColors[agent] : undefined,
                color: ok ? agentColors[agent] : undefined,
              }}
            >
              选 {agent}
              {!ok && <Tag style={{ marginLeft: 6, marginRight: 0 }}>不可用</Tag>}
            </Button>
          );
        })}
        <Button
          icon={<ThunderboltOutlined />}
          disabled={disabled}
          onClick={() => onChoose('auto')}
        >
          帮我选
        </Button>
        <Button
          icon={<ReloadOutlined />}
          disabled={disabled}
          onClick={() => onChoose('regenerate')}
        >
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

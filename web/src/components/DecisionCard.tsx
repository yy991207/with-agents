// 决策卡:让用户在动态 agent 列表 / auto / regenerate 之间选择
// availableAgents 表示哪些 agent 的 think 成功 可被选 失败/取消的灰着不可点
// agent 候选来源:优先 availableAgents 再退化到 round.thinks 的 keys
import { Button, Card, Space, Tag } from 'antd';
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { getAgentColor } from '../theme/tokens';
import { useChat } from '../state/ChatContext';
import type { AgentName } from '../state/types';

export interface DecisionCardProps {
  // 可用的 agent 候选(后端 think_done 时下发) 用于决定按钮排列与可点状态
  agentCandidates: AgentName[];
  onChoose: (choice: AgentName | 'auto' | 'regenerate') => void;
  onCancel?: () => void;
  // 仅这些 agent 可点击 为空/未传则全部可点
  availableAgents?: AgentName[];
  // 判官推荐(judge.done 后端给的)
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

  // 取 agent 的展示名:优先用 settings.drafts 里的 displayName 没有就退到 name
  const labelOf = (name: string): string => {
    const d = state.settings.drafts[name];
    return d?.displayName || name;
  };

  // 没传 availableAgents 视为全部可点
  const allow = (a: AgentName): boolean =>
    !availableAgents || availableAgents.includes(a);

  return (
    <Card
      size="small"
      title="选一个 agent 来回答"
      style={{ margin: '8px 0' }}
      extra={
        judgePick ? (
          <Tag color={getAgentColor(judgePick)}>判官推荐 {labelOf(judgePick)}</Tag>
        ) : null
      }
    >
      <Space wrap>
        {agentCandidates.map((agent) => {
          const ok = allow(agent);
          const color = getAgentColor(agent);
          return (
            <Button
              key={agent}
              disabled={disabled || !ok}
              onClick={() => onChoose(agent)}
              style={{
                borderColor: ok ? color : undefined,
                color: ok ? color : undefined,
              }}
            >
              选 {labelOf(agent)}
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
      </Space>
    </Card>
  );
}

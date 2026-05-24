// think 卡片活跃容器:横向 N 个 ThinkCard 响应式折行
// 数字员工模型重构后:agent 数量动态 渲染基于 round.thinks 的 keys
import { Col, Row } from 'antd';
import ThinkCard from './ThinkCard';
import { agentLabelOf, type AgentLabelMap } from '../state/agentLabels';
import type { AgentName, RoundView } from '../state/types';

export interface ThinkPanelProps {
  round: RoundView;
  agentLabels?: AgentLabelMap;
  onRetry?: (agent: AgentName) => void;
}

// 根据 agent 数量决定每张卡的栅格宽度
// 1 张占满 2 张半屏 3 张 1/3 4+ 走 1/4 并自动折行
function spanFor(count: number): number {
  if (count <= 1) return 24;
  if (count === 2) return 12;
  if (count === 3) return 8;
  return 6;
}

export default function ThinkPanel({
  round,
  agentLabels,
  onRetry,
}: ThinkPanelProps) {
  // 用 round.thinks 的 key 顺序保持稳定 后端塞进来什么顺序就什么顺序
  const agents = Object.keys(round.thinks);
  const md = spanFor(agents.length);

  return (
    <Row gutter={[12, 12]} style={{ margin: '8px 0' }}>
      {agents.map((agent) => (
        <Col key={agent} xs={24} sm={12} md={md}>
          <ThinkCard
            think={round.thinks[agent]}
            agentLabel={agentLabelOf(agentLabels, agent)}
            onRetry={onRetry ? () => onRetry(agent) : undefined}
          />
        </Col>
      ))}
    </Row>
  );
}

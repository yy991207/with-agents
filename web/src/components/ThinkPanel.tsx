// 4 列 think 卡片活跃容器:横向 4 个 ThinkCard,响应式折行
import { Col, Row } from 'antd';
import ThinkCard from './ThinkCard';
import type { AgentName, RoundView } from '../state/types';
import { KNOWN_AGENTS } from '../state/types';

export interface ThinkPanelProps {
  round: RoundView;
  onRetry?: (agent: AgentName) => void;
  onPause?: (agent: AgentName) => void;
}

export default function ThinkPanel({ round, onRetry, onPause }: ThinkPanelProps) {
  return (
    <Row gutter={[12, 12]} style={{ margin: '8px 0' }}>
      {KNOWN_AGENTS.map((agent) => (
        <Col key={agent} xs={24} sm={12} md={6}>
          <ThinkCard
            think={round.thinks[agent]}
            onRetry={onRetry ? () => onRetry(agent) : undefined}
            onPause={onPause ? () => onPause(agent) : undefined}
          />
        </Col>
      ))}
    </Row>
  );
}

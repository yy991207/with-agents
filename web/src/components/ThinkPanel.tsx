// 4 列 think 卡片活跃容器
import { Row, Col } from 'antd';
import ThinkCard from './ThinkCard';
import type { AgentName, RoundView } from '../state/types';

export interface ThinkPanelProps {
  round: RoundView;
  onRetry?: (agent: AgentName) => void;
}

const AGENTS: AgentName[] = ['DeepSeek', 'GLM', 'Kimi', 'Qwen'];

export default function ThinkPanel({ round, onRetry }: ThinkPanelProps) {
  return (
    <Row gutter={12} style={{ margin: '8px 0' }}>
      {AGENTS.map((agent) => (
        <Col key={agent} xs={24} sm={12} md={6}>
          <ThinkCard
            think={round.thinks[agent]}
            onRetry={onRetry ? () => onRetry(agent) : undefined}
          />
        </Col>
      ))}
    </Row>
  );
}

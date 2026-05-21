// think 卡片折叠态(历史轮次):一个小 chip
import { Tag } from 'antd';
import { agentColors } from '../theme/tokens';
import type { ThinkView } from '../state/types';

export interface ThinkCardChipProps {
  think: ThinkView;
}

export default function ThinkCardChip({ think }: ThinkCardChipProps) {
  return (
    <Tag color={agentColors[think.agent]} style={{ marginRight: 6 }}>
      {think.agent} · {think.state}
    </Tag>
  );
}

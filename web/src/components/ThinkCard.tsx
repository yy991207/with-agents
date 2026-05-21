// 单个 think 卡片:展示某个 agent 的 50 字理由,支持暂停/重试
import { Button, Card, Space, Tag } from 'antd';
import { agentColors } from '../theme/tokens';
import type { ThinkView } from '../state/types';

export interface ThinkCardProps {
  think: ThinkView;
  onRetry?: () => void;
  onPause?: () => void;
}

export default function ThinkCard({ think, onRetry, onPause }: ThinkCardProps) {
  const color = agentColors[think.agent];
  return (
    <Card
      size="small"
      title={
        <span style={{ color, fontWeight: 600 }}>
          {think.agent}
        </span>
      }
      style={{ borderTop: `3px solid ${color}` }}
      extra={<Tag>{think.state}</Tag>}
    >
      <div style={{ minHeight: 64, fontSize: 13 }}>
        {think.content ?? (think.error ? `失败:${think.error}` : '思考中...')}
      </div>
      <Space style={{ marginTop: 8 }}>
        <Button size="small" onClick={onPause} disabled={!onPause}>
          暂停
        </Button>
        <Button size="small" onClick={onRetry} disabled={!onRetry}>
          重试
        </Button>
      </Space>
    </Card>
  );
}

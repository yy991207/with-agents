// 用户消息气泡:右对齐
import { Card } from 'antd';

export interface UserBubbleProps {
  content: string;
}

export default function UserBubble({ content }: UserBubbleProps) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '8px 0' }}>
      <Card size="small" style={{ maxWidth: 560, background: '#e3f2fd' }}>
        {content}
      </Card>
    </div>
  );
}

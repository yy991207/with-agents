// 用户消息气泡:右对齐,浅绿底,纯文本展示
import { Card } from 'antd';

export interface UserBubbleProps {
  content: string;
}

export default function UserBubble({ content }: UserBubbleProps) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '8px 0' }}>
      <Card
        size="small"
        style={{
          maxWidth: 560,
          background: '#e8f5e9',
          borderColor: '#c8e6c9',
        }}
      >
        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{content}</div>
      </Card>
    </div>
  );
}

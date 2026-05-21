// 流式回答气泡:markdown 渲染
import { Card } from 'antd';
import ReactMarkdown from 'react-markdown';
import { agentColors } from '../theme/tokens';
import type { ReplyView } from '../state/types';

export interface ReplyBubbleProps {
  reply: ReplyView;
}

export default function ReplyBubble({ reply }: ReplyBubbleProps) {
  const color = agentColors[reply.agent];
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', margin: '8px 0' }}>
      <Card
        size="small"
        title={<span style={{ color }}>{reply.agent}</span>}
        style={{ maxWidth: 720, borderLeft: `3px solid ${color}` }}
      >
        <ReactMarkdown>{reply.content || (reply.state === 'pending' ? '等待中...' : '')}</ReactMarkdown>
      </Card>
    </div>
  );
}

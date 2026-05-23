// 流式回答气泡:按 segments 时间线顺序渲染文本与工具调用
import { Alert, Button, Card, Space, Tag, Typography } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  ReloadOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import { getAgentColor } from '../theme/tokens';
import type { ReplySegment, ReplyView } from '../state/types';

export interface ReplyBubbleProps {
  reply: ReplyView;
  agentLabel?: string;
  onRetry?: () => void;
}

function stateBadge(s: ReplyView['state']): { icon: ReactNode; text: string; color: string } {
  switch (s) {
    case 'pending': return { icon: <LoadingOutlined spin />, text: '等待中', color: 'default' };
    case 'streaming': return { icon: <LoadingOutlined spin />, text: '回答中', color: 'processing' };
    case 'done': return { icon: <CheckCircleOutlined />, text: '完成', color: 'success' };
    case 'failed': return { icon: <CloseCircleOutlined />, text: '失败', color: 'error' };
    case 'cancelled': return { icon: <StopOutlined />, text: '已取消', color: 'default' };
    default: return { icon: null, text: s, color: 'default' };
  }
}

// 工具调用/结果段: 只显示工具名和状态标签
function SegmentItem({ seg }: { seg: ReplySegment }) {
  if (seg.type === 'text') {
    return (
      <div
        className="reply-html"
        style={{ marginBottom: 4 }}
        dangerouslySetInnerHTML={{ __html: seg.content ?? '' }}
      />
    );
  }
  return (
    <div style={{ marginBottom: 4 }}>
      <Space size={4}>
        <ToolOutlined style={{ color: '#1677ff', fontSize: 12 }} />
        <span style={{ fontWeight: 600, fontSize: 13 }}>{seg.tool}</span>
        {seg.type === 'tool_call' && (
          <Tag color="processing" style={{ fontSize: 11, lineHeight: '18px' }}>调用中</Tag>
        )}
        {seg.type === 'tool_result' && (
          <Tag color="default" style={{ fontSize: 11, lineHeight: '18px' }}>完成</Tag>
        )}
      </Space>
    </div>
  );
}

export default function ReplyBubble({ reply, agentLabel, onRetry }: ReplyBubbleProps) {
  const color = getAgentColor(reply.agent);
  const badge = stateBadge(reply.state);
  const title = agentLabel || reply.agent;

  const renderContent = () => {
    if (reply.state === 'failed') {
      return (
        <>
          <Alert type="error" showIcon message="回答失败" description={reply.error || '未知错误'} />
          {onRetry && (
            <Button icon={<ReloadOutlined />} size="small" style={{ marginTop: 8 }} onClick={onRetry}>
              重新回答
            </Button>
          )}
        </>
      );
    }
    if (reply.state === 'cancelled') {
      return (
        <>
          {reply.content ? (
            <div className="reply-html" style={{ lineHeight: 1.7, fontSize: 14, color: 'rgba(0,0,0,0.45)' }} dangerouslySetInnerHTML={{ __html: reply.content }} />
          ) : (
            <Typography.Text type="secondary">已取消</Typography.Text>
          )}
          {onRetry && (
            <Button icon={<ReloadOutlined />} size="small" style={{ marginTop: 8 }} onClick={onRetry}>
              重新回答
            </Button>
          )}
        </>
      );
    }
    if (!reply.content && reply.segments.length === 0) {
      return (
        <Typography.Text type="secondary">
          {reply.state === 'streaming' || reply.state === 'pending' ? '正在生成...' : '(无内容)'}
        </Typography.Text>
      );
    }
    const items = reply.segments.length > 0 ? reply.segments : [{ type: 'text' as const, content: reply.content }];
    return (
      <div style={{ lineHeight: 1.7, fontSize: 14 }}>
        {items.map((seg, i) => (
          <SegmentItem key={i} seg={seg} />
        ))}
        {reply.state === 'streaming' && (
          <span style={{
            display: 'inline-block', width: 8, height: 14, marginLeft: 2,
            background: color, verticalAlign: '-2px',
            animation: 'reply-cursor-blink 1s steps(2) infinite',
          }} />
        )}
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', margin: '8px 0' }}>
      <Card
        size="small"
        title={
          <Space>
            <span style={{ color, fontWeight: 600 }}>{title}</span>
            <Tag color={badge.color}><Space size={4}>{badge.icon}<span>{badge.text}</span></Space></Tag>
          </Space>
        }
        style={{ maxWidth: 760, width: '100%', borderLeft: `3px solid ${color}` }}
      >
        {renderContent()}
      </Card>
      <style>{`@keyframes reply-cursor-blink { 0%,49% { opacity: 1; } 50%,100% { opacity: 0; } }`}</style>
    </div>
  );
}

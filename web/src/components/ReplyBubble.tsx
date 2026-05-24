// 流式回答气泡:按 segments 时间线顺序渲染文本与工具调用
import { Alert, Button, Space, Tag, Typography } from 'antd';
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

function stateBadge(state: ReplyView['state']): { icon: ReactNode; text: string; color: string } {
  switch (state) {
    case 'pending':
      return { icon: <LoadingOutlined spin />, text: '等待中', color: 'default' };
    case 'streaming':
      return { icon: <LoadingOutlined spin />, text: '回答中', color: 'processing' };
    case 'done':
      return { icon: <CheckCircleOutlined />, text: '完成', color: 'success' };
    case 'failed':
      return { icon: <CloseCircleOutlined />, text: '失败', color: 'error' };
    case 'cancelled':
      return { icon: <StopOutlined />, text: '已取消', color: 'default' };
    default:
      return { icon: null, text: state, color: 'default' };
  }
}

function SegmentItem({ segment }: { segment: ReplySegment }) {
  if (segment.type === 'text') {
    return (
      <div
        className="reply-html"
        style={{ marginBottom: 6 }}
        dangerouslySetInnerHTML={{ __html: segment.content ?? '' }}
      />
    );
  }

  return (
    <div
      style={{
        alignItems: 'center',
        background: '#f8fafc',
        border: '1px solid #e5e7eb',
        borderRadius: 12,
        display: 'flex',
        gap: 8,
        marginBottom: 8,
        padding: '10px 12px',
      }}
    >
      <ToolOutlined style={{ color: '#1677ff', fontSize: 12 }} />
      <span style={{ color: 'rgba(15, 23, 42, 0.86)', fontSize: 13, fontWeight: 600 }}>
        {segment.tool}
      </span>
      {segment.type === 'tool_call' ? (
        <Tag color="processing" style={{ borderRadius: 999, margin: 0 }}>
          调用中
        </Tag>
      ) : null}
      {segment.type === 'tool_result' ? (
        <Tag color="default" style={{ borderRadius: 999, margin: 0 }}>
          已完成
        </Tag>
      ) : null}
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
          {onRetry ? (
            <Button icon={<ReloadOutlined />} size="small" shape="round" style={{ marginTop: 12 }} onClick={onRetry}>
              重新回答
            </Button>
          ) : null}
        </>
      );
    }

    if (reply.state === 'cancelled') {
      return (
        <>
          {reply.content ? (
            <div
              className="reply-html"
              style={{ color: 'rgba(51, 65, 85, 0.72)', fontSize: 14, lineHeight: 1.8 }}
              dangerouslySetInnerHTML={{ __html: reply.content }}
            />
          ) : (
            <Typography.Text type="secondary">已取消</Typography.Text>
          )}
          {onRetry ? (
            <Button icon={<ReloadOutlined />} size="small" shape="round" style={{ marginTop: 12 }} onClick={onRetry}>
              重新回答
            </Button>
          ) : null}
        </>
      );
    }

    if (!reply.content && reply.segments.length === 0) {
      return (
        <Typography.Text type="secondary">
          {reply.state === 'streaming' || reply.state === 'pending' ? '正在生成…' : '(无内容)'}
        </Typography.Text>
      );
    }

    const items =
      reply.segments.length > 0
        ? reply.segments
        : [{ type: 'text' as const, content: reply.content }];

    return (
      <div style={{ color: 'rgba(15, 23, 42, 0.88)', fontSize: 14, lineHeight: 1.8 }}>
        {items.map((segment, index) => (
          <SegmentItem key={index} segment={segment} />
        ))}
        {reply.state === 'streaming' ? (
          <span
            style={{
              animation: 'reply-cursor-blink 1s steps(2) infinite',
              background: color,
              display: 'inline-block',
              height: 14,
              marginLeft: 2,
              verticalAlign: '-2px',
              width: 8,
            }}
          />
        ) : null}
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', margin: '8px 0' }}>
      <div
        style={{
          background: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '22px 22px 22px 8px',
          boxShadow: '0 12px 28px rgba(15, 23, 42, 0.06)',
          maxWidth: 780,
          overflow: 'hidden',
          width: '100%',
        }}
      >
        <div
          style={{
            alignItems: 'center',
            borderBottom: '1px solid rgba(226, 232, 240, 0.9)',
            display: 'flex',
            gap: 10,
            justifyContent: 'space-between',
            padding: '12px 14px 10px',
          }}
        >
          <Space size={8}>
            <span
              style={{
                background: color,
                borderRadius: '50%',
                display: 'inline-block',
                height: 8,
                width: 8,
              }}
            />
            <span style={{ color: 'rgba(15, 23, 42, 0.92)', fontWeight: 600 }}>{title}</span>
          </Space>
          <Tag color={badge.color} style={{ borderRadius: 999, margin: 0 }}>
            <Space size={4}>
              {badge.icon}
              <span>{badge.text}</span>
            </Space>
          </Tag>
        </div>
        <div style={{ padding: 14 }}>{renderContent()}</div>
      </div>
    </div>
  );
}

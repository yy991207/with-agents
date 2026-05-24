// 单个 think 卡片:展示某个 agent 的思考要点,根据状态切换不同呈现
import { Alert, Button, Space, Spin, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getAgentColor } from '../theme/tokens';
import type { ThinkView } from '../state/types';

export interface ThinkCardProps {
  think: ThinkView;
  agentLabel?: string;
  onRetry?: () => void;
}

function stateMeta(state: ThinkView['state']): { text: string; color: string } {
  switch (state) {
    case 'pending':
      return { text: '思考中', color: 'processing' };
    case 'done':
      return { text: '完成', color: 'success' };
    case 'failed':
      return { text: '失败', color: 'error' };
    case 'cancelled':
      return { text: '已取消', color: 'default' };
    case 'skipped':
      return { text: '跳过', color: 'default' };
    default:
      return { text: state, color: 'default' };
  }
}

export default function ThinkCard({ think, agentLabel, onRetry }: ThinkCardProps) {
  const color = getAgentColor(think.agent);
  const meta = stateMeta(think.state);
  const title = agentLabel || think.agent;

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 18,
        boxShadow: '0 10px 24px rgba(15, 23, 42, 0.05)',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          alignItems: 'center',
          borderBottom: '1px solid rgba(226, 232, 240, 0.9)',
          display: 'flex',
          justifyContent: 'space-between',
          padding: '12px 14px 10px',
          gap: 12,
        }}
      >
        <div style={{ alignItems: 'center', display: 'flex', gap: 8, minWidth: 0 }}>
          <span
            style={{
              background: color,
              borderRadius: '50%',
              display: 'inline-block',
              flex: '0 0 auto',
              height: 8,
              width: 8,
            }}
          />
          <span
            style={{
              color: 'rgba(15, 23, 42, 0.92)',
              fontSize: 14,
              fontWeight: 600,
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {title}
          </span>
        </div>
        <Tag color={meta.color} style={{ borderRadius: 999, margin: 0 }}>
          {meta.text}
        </Tag>
      </div>

      <div style={{ minHeight: 92, padding: 14 }}>
        {think.state === 'pending' ? (
          <Space>
            <Spin size="small" />
            <span style={{ color: 'rgba(51, 65, 85, 0.72)', fontSize: 13 }}>
              正在整理思考过程…
            </span>
          </Space>
        ) : null}

        {think.state === 'done' ? (
          <div style={{ color: 'rgba(15, 23, 42, 0.88)', fontSize: 13, lineHeight: 1.75, whiteSpace: 'pre-wrap' }}>
            {think.content || '(无内容)'}
          </div>
        ) : null}

        {think.state === 'failed' ? (
          <Alert type="error" showIcon message="思考失败" description={think.error || '未知错误'} />
        ) : null}

        {think.state === 'cancelled' ? (
          <div style={{ color: 'rgba(51, 65, 85, 0.62)', fontSize: 13 }}>本轮思考已取消</div>
        ) : null}

        {think.state === 'skipped' ? (
          <div style={{ color: 'rgba(51, 65, 85, 0.62)', fontSize: 13 }}>本轮思考已跳过</div>
        ) : null}
      </div>

      {(think.state === 'failed' || think.state === 'cancelled') && onRetry ? (
        <div style={{ padding: '0 14px 14px' }}>
          <Button size="small" shape="round" icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        </div>
      ) : null}
    </div>
  );
}

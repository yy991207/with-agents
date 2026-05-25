// 单个 think 卡片:展示某个 agent 的思考要点,根据状态切换不同呈现
// 头部头像优先用 agent 上传的 avatarUrl,否则回退首字母 + 配色色块
// 视觉风格与 ReplyBubble 保持一致
import { Alert, Button, Space, Spin, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { Avatar } from '@lobehub/ui';
import { getAgentColor } from '../theme/tokens';
import type { ThinkView } from '../state/types';

export interface ThinkCardProps {
  think: ThinkView;
  agentLabel?: string;
  // agent 配置里设置的头像 data URL  没有时回退到首字母色块
  avatarUrl?: string | null;
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

export default function ThinkCard({ think, agentLabel, avatarUrl, onRetry }: ThinkCardProps) {
  const color = getAgentColor(think.agent);
  const meta = stateMeta(think.state);
  const title = agentLabel || think.agent;
  const initials = title.slice(0, 1).toUpperCase();

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
          {avatarUrl ? (
            <Avatar
              avatar={avatarUrl}
              shape="square"
              size={24}
              title={title}
              style={{ flex: '0 0 auto' }}
            />
          ) : (
            <Avatar
              background={color}
              shape="square"
              size={24}
              title={title}
              style={{ color: '#fff', flex: '0 0 auto', fontSize: 12, fontWeight: 600 }}
            >
              {initials}
            </Avatar>
          )}
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

// 单个 think 卡片:展示某个 agent 的思考要点,根据状态切换不同呈现
// 支持暂停(取消单 agent)与重试(M2 暂未实装,UI 仅触发提示)
import { Alert, Button, Card, Space, Spin, Tag, Tooltip } from 'antd';
import { PauseOutlined, ReloadOutlined } from '@ant-design/icons';
import { getAgentColor } from '../theme/tokens';
import type { ThinkView } from '../state/types';

export interface ThinkCardProps {
  think: ThinkView;
  agentLabel?: string;
  onRetry?: () => void;
  onPause?: () => void;
}

// 把状态翻成中文 tag,顺便给一个语义色
function stateMeta(s: ThinkView['state']): { text: string; color: string } {
  switch (s) {
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
      return { text: s, color: 'default' };
  }
}

export default function ThinkCard({
  think,
  agentLabel,
  onRetry,
  onPause,
}: ThinkCardProps) {
  const color = getAgentColor(think.agent);
  const meta = stateMeta(think.state);
  const title = agentLabel || think.agent;

  return (
    <Card
      size="small"
      title={<span style={{ color, fontWeight: 600 }}>{title}</span>}
      style={{ borderTop: `3px solid ${color}`, height: '100%' }}
      extra={
        <Space size={6}>
          <Tag color={meta.color} style={{ marginRight: 0 }}>
            {meta.text}
          </Tag>
          {think.state === 'pending' && onPause && (
            <Tooltip title="暂停该 agent">
              <Button
                type="text"
                size="small"
                icon={<PauseOutlined />}
                onClick={onPause}
              />
            </Tooltip>
          )}
        </Space>
      }
    >
      <div style={{ minHeight: 64, fontSize: 13, lineHeight: 1.6 }}>
        {think.state === 'pending' && (
          <Space>
            <Spin size="small" />
            <span style={{ color: 'rgba(0,0,0,0.45)' }}>思考中</span>
          </Space>
        )}
        {think.state === 'done' && (
          <div style={{ whiteSpace: 'pre-wrap' }}>{think.content || '(无内容)'}</div>
        )}
        {think.state === 'failed' && (
          <Alert
            type="error"
            showIcon
            message="失败"
            description={think.error || '未知错误'}
          />
        )}
        {think.state === 'cancelled' && (
          <div style={{ color: 'rgba(0,0,0,0.45)' }}>已取消</div>
        )}
        {think.state === 'skipped' && (
          <div style={{ color: 'rgba(0,0,0,0.45)' }}>已跳过</div>
        )}
      </div>
      {(think.state === 'failed' || think.state === 'cancelled') && (
        <Space style={{ marginTop: 8 }}>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={onRetry}
            disabled={!onRetry}
          >
            重试
          </Button>
        </Space>
      )}
    </Card>
  );
}

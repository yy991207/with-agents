// 流式回答气泡:左对齐,agent 颜色边框,头部带状态,主体 markdown 渲染
// streaming 时尾巴拼一个闪烁光标;toolCalls 走 Collapse 默认折叠
import { Alert, Card, Collapse, Space, Tag, Typography } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  StopOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import { agentColors } from '../theme/tokens';
import type { ReplyView } from '../state/types';

export interface ReplyBubbleProps {
  reply: ReplyView;
}

// reply 状态对应的图标 + 文案
function stateBadge(s: ReplyView['state']): { icon: ReactNode; text: string; color: string } {
  switch (s) {
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
      return { icon: null, text: s, color: 'default' };
  }
}

export default function ReplyBubble({ reply }: ReplyBubbleProps) {
  const color = agentColors[reply.agent];
  const badge = stateBadge(reply.state);

  // streaming 时在结尾追加一个软光标提示用户文本还在长
  const renderContent = () => {
    if (reply.state === 'failed') {
      return (
        <Alert
          type="error"
          showIcon
          message="回答失败"
          description={reply.error || '未知错误'}
        />
      );
    }
    if (!reply.content) {
      return (
        <Typography.Text type="secondary">
          {reply.state === 'streaming' || reply.state === 'pending' ? '正在生成...' : '(无内容)'}
        </Typography.Text>
      );
    }
    return (
      <div className="reply-markdown" style={{ lineHeight: 1.7, fontSize: 14 }}>
        <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
          {reply.content}
        </ReactMarkdown>
        {reply.state === 'streaming' && (
          <span
            style={{
              display: 'inline-block',
              width: 8,
              height: 14,
              marginLeft: 2,
              background: color,
              verticalAlign: '-2px',
              animation: 'reply-cursor-blink 1s steps(2) infinite',
            }}
          />
        )}
      </div>
    );
  };

  // toolCalls 折叠面板:每个 call 一项
  const collapseItems = reply.toolCalls.map((c, idx) => ({
    key: String(idx),
    label: (
      <Space>
        <Tag color="blue" style={{ marginRight: 0 }}>
          tool
        </Tag>
        <span>{c.tool}</span>
      </Space>
    ),
    children: (
      <div style={{ fontSize: 12 }}>
        {c.input && (
          <div style={{ marginBottom: 8 }}>
            <strong>输入</strong>
            <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
              {c.input}
            </pre>
          </div>
        )}
        {c.result !== undefined && (
          <div>
            <strong>结果</strong>
            <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
              {c.result}
            </pre>
          </div>
        )}
      </div>
    ),
  }));

  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', margin: '8px 0' }}>
      <Card
        size="small"
        title={
          <Space>
            <span style={{ color, fontWeight: 600 }}>{reply.agent}</span>
            <Tag color={badge.color}>
              <Space size={4}>
                {badge.icon}
                <span>{badge.text}</span>
              </Space>
            </Tag>
          </Space>
        }
        style={{ maxWidth: 760, width: '100%', borderLeft: `3px solid ${color}` }}
      >
        {renderContent()}
        {collapseItems.length > 0 && (
          <Collapse
            size="small"
            ghost
            style={{ marginTop: 12 }}
            items={collapseItems}
          />
        )}
      </Card>
      {/* keyframes 一次性注入 用 inline style 写到根 head 里太重 这里靠全局 css 兜底 */}
      <style>{`@keyframes reply-cursor-blink { 0%,49% { opacity: 1; } 50%,100% { opacity: 0; } }`}</style>
    </div>
  );
}

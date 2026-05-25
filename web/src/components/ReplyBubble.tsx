// AI 回复:对齐 LobeChat ChatItem 的扁平布局
// 头像 + 名字 + 时间 inline,内容直接铺背景,无卡片边框/阴影
// segments 里的 tool_call/tool_result 合并成可折叠 accordion 嵌入正文流
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Alert, Button, Collapse, Typography } from 'antd';
import { Avatar } from '@lobehub/ui';
import { Flexbox } from 'react-layout-kit';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { getAgentColor } from '../theme/tokens';
import type { ReplySegment, ReplyView } from '../state/types';

export interface ReplyBubbleProps {
  reply: ReplyView;
  agentLabel?: string;
  // agent 配置里设置的头像 data URL  没有时回退到首字母色块
  avatarUrl?: string | null;
  onRetry?: () => void;
}

// segments 时间线节点:文本块或工具调用块
type TimelineNode =
  | { kind: 'text'; content: string }
  | { kind: 'tool'; tool: string; input?: string; result?: string; finished: boolean };

// 把 [text, tool_call, tool_result, text...] 按时间顺序合并成 [text, tool, text]
// 同名 tool 的 call+result 合并为一个 tool 节点
function buildTimeline(segments: ReplySegment[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];
  for (const seg of segments) {
    if (seg.type === 'text') {
      const last = nodes[nodes.length - 1];
      if (last && last.kind === 'text') {
        last.content += seg.content ?? '';
      } else {
        nodes.push({ kind: 'text', content: seg.content ?? '' });
      }
      continue;
    }
    if (seg.type === 'tool_call') {
      nodes.push({
        kind: 'tool',
        tool: seg.tool ?? '',
        input: seg.input,
        finished: false,
      });
      continue;
    }
    if (seg.type === 'tool_result') {
      // 找最近一个未完成且同名的 tool 节点合并
      for (let i = nodes.length - 1; i >= 0; i -= 1) {
        const node = nodes[i];
        if (node.kind === 'tool' && node.tool === seg.tool && !node.finished) {
          node.result = seg.result;
          node.finished = true;
          break;
        }
      }
      // 没找到匹配 call(数据异常时兜底): 直接挂一个已完成的 tool 节点
      const last = nodes[nodes.length - 1];
      if (!last || last.kind !== 'tool' || last.tool !== seg.tool || !last.finished) {
        nodes.push({
          kind: 'tool',
          tool: seg.tool ?? '',
          result: seg.result,
          finished: true,
        });
      }
    }
  }
  return nodes;
}

// 单个工具调用折叠块:头部 = 状态图标 + 工具名 + (展开时的箭头),内容 = input/result
function ToolAccordion({ node }: { node: Extract<TimelineNode, { kind: 'tool' }> }) {
  const [open, setOpen] = useState(false);
  const icon = node.finished ? (
    <CheckCircleOutlined style={{ color: 'var(--ant-color-success)', fontSize: 14 }} />
  ) : (
    <LoadingOutlined spin style={{ color: 'var(--ant-color-primary)', fontSize: 14 }} />
  );
  const statusText = node.finished ? '已完成' : '调用中';
  const items = [
    {
      key: node.tool,
      label: (
        <Flexbox horizontal align="center" gap={8} style={{ minWidth: 0 }}>
          {icon}
          <span
            style={{
              color: 'rgba(15, 23, 42, 0.86)',
              fontSize: 13,
              fontWeight: 500,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {node.tool || '工具调用'}
          </span>
          <span
            style={{ color: 'rgba(71, 85, 105, 0.56)', flexShrink: 0, fontSize: 12 }}
          >
            {statusText}
          </span>
        </Flexbox>
      ),
      children: (
        <Flexbox gap={6} style={{ fontSize: 12 }}>
          {node.input ? (
            <div>
              <div style={{ color: 'rgba(71, 85, 105, 0.72)', marginBottom: 2 }}>入参</div>
              <pre
                style={{
                  background: 'rgba(15, 23, 42, 0.04)',
                  borderRadius: 8,
                  margin: 0,
                  maxHeight: 160,
                  overflow: 'auto',
                  padding: '8px 10px',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {node.input}
              </pre>
            </div>
          ) : null}
          {node.result ? (
            <div>
              <div style={{ color: 'rgba(71, 85, 105, 0.72)', marginBottom: 2 }}>结果</div>
              <pre
                style={{
                  background: 'rgba(15, 23, 42, 0.04)',
                  borderRadius: 8,
                  margin: 0,
                  maxHeight: 240,
                  overflow: 'auto',
                  padding: '8px 10px',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {node.result}
              </pre>
            </div>
          ) : null}
        </Flexbox>
      ),
    },
  ];
  return (
    <Collapse
      activeKey={open ? [node.tool] : []}
      bordered={false}
      ghost
      items={items}
      size="small"
      style={{ background: 'transparent' }}
      onChange={(keys) => setOpen(keys.length > 0)}
    />
  );
}

function TextBlock({ html }: { html: string }) {
  return (
    <article
      className="reply-html lobe-md"
      // 后端模型直接吐 HTML(见 commit 2074801),所以走 innerHTML 渲染
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export default function ReplyBubble({ reply, agentLabel, avatarUrl, onRetry }: ReplyBubbleProps) {
  const color = getAgentColor(reply.agent);
  const title = agentLabel || reply.agent;
  const initials = title.slice(0, 1).toUpperCase();
  const isStreaming = reply.state === 'streaming' || reply.state === 'pending';

  const timeline = useMemo(() => buildTimeline(reply.segments), [reply.segments]);

  let body: ReactNode;
  if (reply.state === 'failed') {
    body = (
      <Flexbox gap={10}>
        <Alert
          description={reply.error || '未知错误'}
          message="回答失败"
          showIcon
          type="error"
        />
        {onRetry ? (
          <Button
            icon={<ReloadOutlined />}
            onClick={onRetry}
            shape="round"
            size="small"
            style={{ alignSelf: 'flex-start' }}
          >
            重新回答
          </Button>
        ) : null}
      </Flexbox>
    );
  } else if (timeline.length === 0 && !reply.content) {
    body = (
      <Typography.Text type="secondary">
        {isStreaming ? '正在生成…' : '(无内容)'}
      </Typography.Text>
    );
  } else {
    // 有 segments 走时间线;否则把整段 content 当一个文本节点
    const nodes: TimelineNode[] =
      timeline.length > 0 ? timeline : [{ kind: 'text', content: reply.content }];
    body = (
      <Flexbox gap={8}>
        {nodes.map((node, i) =>
          node.kind === 'text' ? (
            <TextBlock html={node.content} key={`t-${i}`} />
          ) : (
            <ToolAccordion key={`x-${i}-${node.tool}`} node={node} />
          ),
        )}
        {reply.state === 'cancelled' ? (
          <Flexbox horizontal align="center" gap={8}>
            <StopOutlined style={{ color: 'rgba(71, 85, 105, 0.56)' }} />
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              已取消
            </Typography.Text>
            {onRetry ? (
              <Button
                icon={<ReloadOutlined />}
                onClick={onRetry}
                shape="round"
                size="small"
              >
                重新回答
              </Button>
            ) : null}
          </Flexbox>
        ) : null}
        {isStreaming ? (
          <span
            aria-hidden
            style={{
              animation: 'reply-cursor-blink 1s steps(2) infinite',
              background: color,
              borderRadius: 2,
              display: 'inline-block',
              height: 14,
              marginLeft: 0,
              verticalAlign: '-2px',
              width: 6,
            }}
          />
        ) : null}
      </Flexbox>
    );
  }

  return (
    <Flexbox
      align="flex-start"
      className="lobe-chat-item-left"
      gap={8}
      paddingBlock={8}
      style={{ width: '100%' }}
    >
      {/* 头部:头像 + 名字 + 状态 */}
      <Flexbox horizontal align="center" gap={8}>
        {avatarUrl ? (
          <Avatar
            avatar={avatarUrl}
            shape="square"
            size={28}
            title={title}
          />
        ) : (
          <Avatar
            background={color}
            shape="square"
            size={28}
            style={{ color: '#fff', fontSize: 13, fontWeight: 600 }}
            title={title}
          >
            {initials}
          </Avatar>
        )}
        <span style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 14, fontWeight: 500 }}>
          {title}
        </span>
        {isStreaming ? (
          <span style={{ color: 'rgba(71, 85, 105, 0.56)', fontSize: 12 }}>
            <LoadingOutlined spin /> 生成中
          </span>
        ) : reply.state === 'done' ? (
          <CheckCircleOutlined style={{ color: 'rgba(71, 85, 105, 0.45)', fontSize: 12 }} />
        ) : reply.state === 'failed' ? (
          <CloseCircleOutlined style={{ color: 'var(--ant-color-error)', fontSize: 12 }} />
        ) : null}
      </Flexbox>
      {/* 正文 */}
      <Flexbox gap={8} style={{ maxWidth: '100%', width: '100%' }}>
        {body}
      </Flexbox>
    </Flexbox>
  );
}

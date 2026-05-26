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

// segments 时间线节点  thinking / text / tool 三类
//   thinking  reasoning model 的深度思考  独立成块  默认折叠
//   text      LLM 正文  按段拼回原文展示
//   tool      工具调用 + 结果  同名 call+result 合并成一个节点
type TimelineNode =
  | { kind: 'thinking'; content: string }
  | { kind: 'text'; content: string }
  | { kind: 'tool'; tool: string; input?: string; result?: string; finished: boolean };

// 把 [thinking, text, tool_call, tool_result, text, ...] 按时间顺序合并成 [thinking, text, tool, text, ...]
// 同名 tool 的 call+result 合并为一个 tool 节点  连续 thinking / text 段也按相邻合并
function buildTimeline(segments: ReplySegment[]): TimelineNode[] {
  const nodes: TimelineNode[] = [];
  for (const seg of segments) {
    if (seg.type === 'thinking') {
      const last = nodes[nodes.length - 1];
      if (last && last.kind === 'thinking') {
        last.content += seg.content ?? '';
      } else {
        nodes.push({ kind: 'thinking', content: seg.content ?? '' });
      }
      continue;
    }
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

// 字节大小友好显示  仅按字符数估算  仅用于工具结果概要 chip
// 12345 → "12.1 KB"  900 → "900 B"  1024 * 1024 + → "X.X MB"
function formatSize(s?: string): string {
  if (!s) return '';
  const n = s.length;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 入参摘要  从 JSON 对象里挑第一个有意义的 string/number 字段做 chip 副标
// 失败 / 非对象  截断到 60 字符返回  避免 chip 撑爆
function summarizeInput(input?: string): string {
  if (!input) return '';
  try {
    const obj = JSON.parse(input);
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      // 优先取 url / path / file_path / query / command 这些"主参"
      const priorityKeys = [
        'url', 'path', 'file_path', 'filePath', 'query', 'q',
        'command', 'cmd', 'name', 'pattern', 'text',
      ];
      for (const k of priorityKeys) {
        const v = (obj as Record<string, unknown>)[k];
        if (typeof v === 'string' && v) {
          const truncated = v.length > 60 ? v.slice(0, 60) + '…' : v;
          return `${k}="${truncated}"`;
        }
      }
      // 没命中  取第一个 string / number 字段
      for (const [k, v] of Object.entries(obj)) {
        if (typeof v === 'string' && v) {
          const truncated = v.length > 60 ? v.slice(0, 60) + '…' : v;
          return `${k}="${truncated}"`;
        }
        if (typeof v === 'number') return `${k}=${v}`;
      }
    }
  } catch {
    // 非 JSON  直接截
  }
  const s = String(input);
  return s.length > 60 ? s.slice(0, 60) + '…' : s;
}

// 检测一段字符串大概是哪种 payload  用于工具结果的渲染分支
//   json   能 JSON.parse 成对象 / 数组
//   html   开头是 <!DOCTYPE / <html / <svg
//   text   其它一律当文本
type PayloadKind = 'json' | 'html' | 'text';
function detectPayloadKind(s: string): PayloadKind {
  if (!s) return 'text';
  const trimmed = s.trimStart();
  // HTML / SVG 检测  忽略大小写
  if (
    trimmed.startsWith('<!DOCTYPE') ||
    trimmed.startsWith('<!doctype') ||
    trimmed.toLowerCase().startsWith('<html') ||
    trimmed.toLowerCase().startsWith('<svg')
  ) {
    return 'html';
  }
  // JSON 检测  开头必须是 { 或 [
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const v = JSON.parse(trimmed);
      if (v && typeof v === 'object') return 'json';
    } catch {
      // ignore
    }
  }
  return 'text';
}

// 通用滚动框样式  入参 / 结果 / 文本 都用这套
const PAYLOAD_PRE_STYLE = {
  background: 'rgba(15, 23, 42, 0.04)',
  borderRadius: 8,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  fontSize: 12,
  lineHeight: 1.6,
  margin: 0,
  maxHeight: 240,
  overflow: 'auto',
  padding: '8px 10px',
  whiteSpace: 'pre-wrap' as const,
  wordBreak: 'break-word' as const,
};

// 在浏览器新窗口预览 HTML  Blob URL 用完就 revoke  防内存泄露
function openHtmlPreview(html: string) {
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const win = window.open(url, '_blank', 'noopener,noreferrer');
  // 让浏览器一段时间后自动回收  即使 win 为 null 也无所谓
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  if (!win) {
    // 弹窗被拦截时友好提示
    // eslint-disable-next-line no-alert
    alert('预览窗口被浏览器拦截 请允许此站点的弹出窗口');
  }
}

// 智能渲染工具入参 / 结果
//   payload 为空 → 不渲染
//   JSON       → 缩进 2 空格美化字符串
//   HTML       → "HTML XX KB" 提示 + 在新窗口预览按钮 + 折叠 pre 看源码
//   text       → 直接 pre  超长走滚动
interface ToolPayloadViewProps {
  label: '入参' | '结果';
  payload?: string;
}
function ToolPayloadView({ label, payload }: ToolPayloadViewProps) {
  const [showRawHtml, setShowRawHtml] = useState(false);
  if (!payload) return null;
  const kind = detectPayloadKind(payload);

  let body: ReactNode;
  if (kind === 'json') {
    let pretty = payload;
    try {
      pretty = JSON.stringify(JSON.parse(payload), null, 2);
    } catch {
      // 兜底用原样
    }
    body = <pre style={PAYLOAD_PRE_STYLE}>{pretty}</pre>;
  } else if (kind === 'html') {
    body = (
      <Flexbox gap={6}>
        <Flexbox horizontal align="center" gap={8} style={{ fontSize: 12 }}>
          <span
            style={{
              background: 'rgba(59, 130, 246, 0.12)',
              borderRadius: 4,
              color: '#2563eb',
              fontWeight: 500,
              padding: '1px 6px',
            }}
          >
            HTML
          </span>
          <span style={{ color: 'rgba(71, 85, 105, 0.72)' }}>
            {formatSize(payload)}
          </span>
          <Button
            onClick={() => openHtmlPreview(payload)}
            size="small"
            type="link"
            style={{ height: 'auto', padding: 0 }}
          >
            在新窗口预览
          </Button>
          <Button
            onClick={() => setShowRawHtml((v) => !v)}
            size="small"
            type="link"
            style={{ height: 'auto', padding: 0 }}
          >
            {showRawHtml ? '收起源码' : '查看源码'}
          </Button>
        </Flexbox>
        {showRawHtml ? <pre style={PAYLOAD_PRE_STYLE}>{payload}</pre> : null}
      </Flexbox>
    );
  } else {
    body = <pre style={PAYLOAD_PRE_STYLE}>{payload}</pre>;
  }

  return (
    <div>
      <div style={{ color: 'rgba(71, 85, 105, 0.72)', fontSize: 12, marginBottom: 4 }}>
        {label}
      </div>
      {body}
    </div>
  );
}

// 单个工具调用折叠块  IDE chip 风
//   折叠态:  ✓ tool_name  key="val"  12.3 KB
//   展开态:  入参 (JSON 美化) + 结果 (智能识别 HTML / JSON / text)
function ToolAccordion({ node }: { node: Extract<TimelineNode, { kind: 'tool' }> }) {
  const [open, setOpen] = useState(false);
  const icon = node.finished ? (
    <CheckCircleOutlined style={{ color: '#10b981', fontSize: 13 }} />
  ) : (
    <LoadingOutlined spin style={{ color: 'var(--ant-color-primary)', fontSize: 13 }} />
  );
  const inputSummary = summarizeInput(node.input);
  const sizeText = node.finished ? formatSize(node.result) : '';

  const items = [
    {
      key: node.tool,
      label: (
        <Flexbox horizontal align="center" gap={10} style={{ minWidth: 0 }}>
          {icon}
          <span
            style={{
              color: 'rgba(15, 23, 42, 0.86)',
              fontSize: 13,
              fontWeight: 500,
              flexShrink: 0,
            }}
          >
            {node.tool || '工具调用'}
          </span>
          {inputSummary ? (
            <span
              style={{
                color: 'rgba(71, 85, 105, 0.7)',
                fontFamily:
                  'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                fontSize: 12,
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {inputSummary}
            </span>
          ) : null}
          {sizeText ? (
            <span
              style={{
                color: 'rgba(71, 85, 105, 0.5)',
                fontSize: 11,
                flexShrink: 0,
                marginLeft: 'auto',
              }}
            >
              {sizeText}
            </span>
          ) : null}
        </Flexbox>
      ),
      children: (
        <Flexbox gap={8}>
          <ToolPayloadView label="入参" payload={node.input} />
          <ToolPayloadView label="结果" payload={node.result} />
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

// 深度思考块  reasoning model 的 reasoning_content 单独成一段
//   折叠态: 大脑图标 + "深度思考" + 字符数  视觉与工具调用 chip 完全一致
//   展开态: monospace pre  背景与 ToolAccordion 的入参 / 结果 pre 同款灰
//   流式中: 默认展开  done 后回归折叠 但不强制覆盖用户手动展开后的状态
interface ThinkingBlockProps {
  content: string;
  streaming: boolean;
}
function ThinkingBlock({ content, streaming }: ThinkingBlockProps) {
  const [open, setOpen] = useState(streaming);
  const charCount = content.length;
  const items = [
    {
      key: 'thinking',
      label: (
        <Flexbox horizontal align="center" gap={8} style={{ minWidth: 0 }}>
          {/* 大脑图标 与 ChatInput 同款  色调对齐 ToolAccordion 不再蓝色 */}
          <span
            style={{
              color: 'rgba(71, 85, 105, 0.7)',
              fontSize: 13,
              lineHeight: 1,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path
                d="M9.5 2A3.5 3.5 0 0 0 6 5.5v.06a3.5 3.5 0 0 0-2 6.39A3.5 3.5 0 0 0 6 18a3.5 3.5 0 0 0 6 1.5V4.06A3.5 3.5 0 0 0 9.5 2Z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                fill="none"
              />
              <path
                d="M14.5 2A3.5 3.5 0 0 1 18 5.5v.06a3.5 3.5 0 0 1 2 6.39A3.5 3.5 0 0 1 18 18a3.5 3.5 0 0 1-6 1.5V4.06A3.5 3.5 0 0 1 14.5 2Z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
                fill="none"
              />
            </svg>
          </span>
          <span
            style={{
              color: 'rgba(15, 23, 42, 0.86)',
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            深度思考
          </span>
          {streaming ? (
            <span style={{ color: 'rgba(71, 85, 105, 0.56)', fontSize: 12 }}>
              <LoadingOutlined spin /> 推理中
            </span>
          ) : (
            <span style={{ color: 'rgba(71, 85, 105, 0.5)', fontSize: 11 }}>
              {charCount} 字
            </span>
          )}
        </Flexbox>
      ),
      children: (
        <pre style={PAYLOAD_PRE_STYLE}>{content}</pre>
      ),
    },
  ];
  return (
    <Collapse
      activeKey={open ? ['thinking'] : []}
      bordered={false}
      ghost
      items={items}
      size="small"
      style={{ background: 'transparent' }}
      onChange={(keys) => setOpen(keys.length > 0)}
    />
  );
}

// ISO 字符串 → 本地 HH:mm 显示  失败兜底空串避免渲染异常
// 兼容性: 如果后端字符串没带时区  按 UTC 解释  防止历史 mongo naive datetime 导致显示晚 8 小时
function formatTime(iso?: string): string {
  if (!iso) return '';
  const hasTZ = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso);
  const d = new Date(hasTZ ? iso : iso + 'Z');
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export default function ReplyBubble({ reply, agentLabel, avatarUrl, onRetry }: ReplyBubbleProps) {
  const color = getAgentColor(reply.agent);
  const title = agentLabel || reply.agent;
  const initials = title.slice(0, 1).toUpperCase();
  const isStreaming = reply.state === 'streaming' || reply.state === 'pending';
  // reply 完成时间 来自后端 reply.finished_at  流式期间没值不显示
  const timeText = formatTime(reply.finishedAt);

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
        {nodes.map((node, i) => {
          if (node.kind === 'thinking') {
            return (
              <ThinkingBlock
                content={node.content}
                key={`th-${i}`}
                streaming={isStreaming}
              />
            );
          }
          if (node.kind === 'text') {
            return <TextBlock html={node.content} key={`t-${i}`} />;
          }
          return <ToolAccordion key={`x-${i}-${node.tool}`} node={node} />;
        })}
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
      {/* 头部:头像 + 名字 + 时间 + 状态 */}
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
        {timeText ? (
          <span style={{ color: 'rgba(71, 85, 105, 0.5)', fontSize: 11 }}>
            {timeText}
          </span>
        ) : null}
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

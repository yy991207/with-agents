// AI 回复:对齐 LobeChat ChatItem 的扁平布局
// 头像 + 名字 + 时间 inline,内容直接铺背景,无卡片边框/阴影
// segments 里的 tool_call/tool_result 合并成可折叠 accordion 嵌入正文流
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Collapse, Popover, Tooltip, Typography } from 'antd';
import { Avatar } from '@lobehub/ui';
import { Flexbox } from 'react-layout-kit';
import {
  CheckOutlined,
  CloseCircleOutlined,
  ExpandAltOutlined,
  ForkOutlined,
  LoadingOutlined,
  ReloadOutlined,
  RetweetOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { getAgentColor } from '../theme/tokens';
import type { ReplySegment, ReplyView } from '../state/types';
import type { AgentMetaMap } from '../state/agentLabels';

export interface ReplyBubbleProps {
  reply: ReplyView;
  agentLabel?: string;
  // agent 配置里设置的头像 data URL  没有时回退到首字母色块
  avatarUrl?: string | null;
  // 重答  reply 进入终态(done/failed/cancelled) 时显示
  onRetry?: () => void;
  // 终止  reply 还在 streaming/pending 时显示
  onCancel?: () => void;
  // 放大全屏  无值时不显示放大按钮
  onFullscreen?: () => void;
  // 从该 assistant 回复创建分支会话
  onBranch?: () => void;
  // 同轮可切换查看的所有 replies  key=agent
  replyOptions?: ReplyView[];
  agentMetas?: AgentMetaMap;
  onSwitchAgent?: (agent: string) => void;
  // 是否处于全屏模式  全屏模式下不再显示放大按钮  且 maxHeight 放大
  fullscreen?: boolean;
  // 该子窗是否为本轮选定的正式回答  multi 模式下用于在头像旁加灰色对号徽标
  // 单 agent 模式自然就是选中  没必要单独标记
  selected?: boolean;
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
//   json   能 JSON.parse 成对象 / 数组   走美化缩进
//   text   其它一律当文本   原样 pre 显示
// 历史曾支持 html 分支(预览按钮 + 查看源码)  应需求移除  HTML 当文本展示即可
type PayloadKind = 'json' | 'text';
function detectPayloadKind(s: string): PayloadKind {
  if (!s) return 'text';
  const trimmed = s.trimStart();
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

// 智能渲染工具入参 / 结果
//   payload 为空 → 不渲染
//   JSON       → 缩进 2 空格美化字符串
//   text       → 直接 pre  超长走滚动 (HTML 也走这个分支)
interface ToolPayloadViewProps {
  label: '入参' | '结果';
  payload?: string;
}
function ToolPayloadView({ label, payload }: ToolPayloadViewProps) {
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
  // finished 时不再渲染绿色对号  保持 chip 干净  仅 loading 状态显示转圈
  const icon = node.finished ? null : (
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

interface ReplyAgentSwitcherProps {
  currentAgent: string;
  replyOptions: ReplyView[];
  agentMetas?: AgentMetaMap;
  onSwitchAgent: (agent: string) => void;
}
function ReplyAgentSwitcher({
  currentAgent,
  replyOptions,
  agentMetas,
  onSwitchAgent,
}: ReplyAgentSwitcherProps) {
  const [open, setOpen] = useState(false);
  if (replyOptions.length <= 1) return null;
  const content = (
    <div style={{ minWidth: 140, maxWidth: 220 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {replyOptions.map((option) => {
          const active = option.agent === currentAgent;
          const avatarUrl = agentMetas?.[option.agent]?.avatarDataUrl ?? null;
          const label = agentMetas?.[option.agent]?.displayName || option.agent;
          return (
            <button
              key={option.agent}
              type="button"
              onClick={() => {
                onSwitchAgent(option.agent);
                setOpen(false);
              }}
              style={{
                alignItems: 'center',
                background: active ? 'rgba(15, 23, 42, 0.06)' : 'transparent',
                border: 'none',
                borderRadius: 8,
                color: 'rgba(15, 23, 42, 0.88)',
                cursor: 'pointer',
                display: 'flex',
                gap: 8,
                padding: '6px 8px',
                textAlign: 'left',
                width: '100%',
              }}
            >
              {avatarUrl ? (
                <img
                  src={avatarUrl}
                  alt={label}
                  style={{ borderRadius: 6, height: 20, objectFit: 'cover', width: 20 }}
                />
              ) : (
                <span
                  style={{
                    alignItems: 'center',
                    background: 'rgba(15, 23, 42, 0.08)',
                    borderRadius: 6,
                    display: 'inline-flex',
                    fontSize: 11,
                    fontWeight: 600,
                    height: 20,
                    justifyContent: 'center',
                    width: 20,
                  }}
                >
                  {label.slice(0, 1).toUpperCase()}
                </span>
              )}
              <span style={{ flex: 1, fontSize: 13 }}>{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomLeft"
    >
      <Button
        aria-label="切换本轮 agent 回复"
        icon={<RetweetOutlined />}
        size="small"
        type="text"
        shape="circle"
      />
    </Popover>
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
//   折叠态: 大脑图标 + Thinking… / Thought · Xs · Y 字   视觉与工具调用 chip 完全一致
//   展开态: monospace pre  背景与 ToolAccordion 的入参 / 结果 pre 同款灰
//   流式中: 默认展开  done 后回归折叠 但不强制覆盖用户手动展开后的状态
//   耗时:   useEffect 在第一次 content 到来时记 startTime  streaming 切 false 时算 elapsed
//          页面刷新后历史轮次 streaming 一开始就是 false  无法还原 startTime  只显示字数
interface ThinkingBlockProps {
  content: string;
  streaming: boolean;
}
function ThinkingBlock({ content, streaming }: ThinkingBlockProps) {
  const [open, setOpen] = useState(streaming);
  // elapsedMs: 流式结束后通过 useEffect 计算  null 表示未知 (历史轮次刷新后)
  const [startTime, setStartTime] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);

  // 流式中  收到第一份 reasoning content 时打 startTime
  // 不在 mount 时就计  避免空 reasoning 还在等的瞬间被错算
  useEffect(() => {
    if (streaming && content.length > 0 && startTime === null) {
      setStartTime(Date.now());
    }
  }, [streaming, content.length, startTime]);

  // streaming 由 true 切 false 时  锁定 elapsed
  useEffect(() => {
    if (!streaming && startTime !== null && elapsedMs === null) {
      setElapsedMs(Date.now() - startTime);
    }
  }, [streaming, startTime, elapsedMs]);

  const charCount = content.length;
  // 完成态副标  Thought · 5s  历史轮次没 elapsed 就只显 Thought
  // 字数统计删除  视觉更克制  charCount 仅保留以备用
  void charCount;
  const renderDoneSubtitle = (): string => {
    const parts: string[] = ['Thought'];
    if (elapsedMs !== null) {
      const seconds = Math.max(1, Math.round(elapsedMs / 1000));
      parts.push(`${seconds}s`);
    }
    return parts.join(' · ');
  };

  const items = [
    {
      key: 'thinking',
      label: (
        <Flexbox horizontal align="center" gap={8} style={{ minWidth: 0 }}>
          {streaming ? (
            <>
              <span
                style={{
                  color: 'rgba(15, 23, 42, 0.86)',
                  fontSize: 13,
                  fontWeight: 500,
                }}
              >
                Thinking…
              </span>
              <LoadingOutlined
                spin
                style={{ color: 'rgba(71, 85, 105, 0.56)', fontSize: 12 }}
              />
            </>
          ) : (
            <span
              style={{
                color: 'rgba(15, 23, 42, 0.86)',
                fontSize: 13,
                fontWeight: 500,
              }}
            >
              {renderDoneSubtitle()}
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

export default function ReplyBubble({
  reply,
  agentLabel,
  avatarUrl,
  onRetry,
  onCancel,
  onFullscreen,
  onBranch,
  replyOptions,
  agentMetas,
  onSwitchAgent,
  fullscreen = false,
  selected = false,
}: ReplyBubbleProps) {
  const color = getAgentColor(reply.agent);
  const title = agentLabel || reply.agent;
  const initials = title.slice(0, 1).toUpperCase();
  const isStreaming = reply.state === 'streaming' || reply.state === 'pending';
  // reply 完成时间 来自后端 reply.finished_at  流式期间没值不显示
  const timeText = formatTime(reply.finishedAt);

  const timeline = useMemo(() => buildTimeline(reply.segments), [reply.segments]);

  let body: ReactNode;
  if (reply.state === 'failed') {
    // 失败态  纯文字风  无背景容器
    //   保留错误提示和错误摘要
    //   重新回答按钮复用头部 toolbar 的通用位置逻辑
    //   头部不再重复显示一份失败状态图标
    const errorText = reply.error || '未知错误';
    body = (
      <Flexbox horizontal align="center" gap={10} style={{ minWidth: 0 }}>
        <CloseCircleOutlined
          style={{ color: 'var(--ant-color-error)', fontSize: 13, flexShrink: 0 }}
        />
        <span
          style={{
            color: 'rgba(15, 23, 42, 0.86)',
            fontSize: 13,
            fontWeight: 500,
            flexShrink: 0,
          }}
        >
          回答失败
        </span>
        <span
          title={errorText}
          style={{
            color: 'rgba(71, 85, 105, 0.7)',
            fontFamily:
              'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            fontSize: 12,
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {errorText}
        </span>
      </Flexbox>
    );
  } else if (reply.retrying) {
    // 限流重试中  展示重试状态
    const { attempt, maxRetries, delayS } = reply.retrying;
    body = (
      <Flexbox horizontal align="center" gap={10} style={{ minWidth: 0 }}>
        <LoadingOutlined style={{ fontSize: 13, flexShrink: 0 }} />
        <span
          style={{
            color: 'rgba(15, 23, 42, 0.86)',
            fontSize: 13,
            fontWeight: 500,
            flexShrink: 0,
          }}
        >
          限流重试中
        </span>
        <span
          style={{
            color: 'rgba(71, 85, 105, 0.7)',
            fontSize: 12,
            flexShrink: 0,
          }}
        >
          第 {attempt}/{maxRetries} 次
          {delayS > 0 ? `，约 ${delayS < 1 ? '<1' : Math.round(delayS)} 秒后重试` : ''}
        </span>
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
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            cancelled
          </Typography.Text>
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
      {/* 头部:头像 + 名字 + 时间 + 状态 + toolbar */}
      <Flexbox horizontal align="center" gap={8} style={{ width: '100%' }}>
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
        {selected ? (
          <span
            aria-label="已选定为正式回答"
            title="已选定为正式回答"
            style={{
              alignItems: 'center',
              background: '#fff',
              border: '1px solid rgba(15, 23, 42, 0.36)',
              borderRadius: '50%',
              color: 'rgba(15, 23, 42, 0.7)',
              display: 'inline-flex',
              height: 14,
              justifyContent: 'center',
              width: 14,
            }}
          >
            <CheckOutlined style={{ fontSize: 9 }} />
          </span>
        ) : null}
        {fullscreen && replyOptions && onSwitchAgent ? (
          <ReplyAgentSwitcher
            currentAgent={reply.agent}
            replyOptions={replyOptions}
            agentMetas={agentMetas}
            onSwitchAgent={onSwitchAgent}
          />
        ) : null}
        {timeText ? (
          <span style={{ color: 'rgba(71, 85, 105, 0.5)', fontSize: 11 }}>
            {timeText}
          </span>
        ) : null}
        {!isStreaming && onBranch ? (
          <span className="reply-bubble-hover-inline-action">
            <Tooltip title="从这条回答创建分支会话">
              <Button
                aria-label="从这条回答创建分支会话"
                className="reply-bubble-action-button"
                icon={<ForkOutlined />}
                onClick={onBranch}
                size="small"
                type="text"
                shape="circle"
              />
            </Tooltip>
          </span>
        ) : null}
        {isStreaming ? (
          <span style={{ color: 'rgba(71, 85, 105, 0.56)', fontSize: 12 }}>
            <LoadingOutlined spin /> 生成中
          </span>
        ) : null}
        {/* 会话操作  贴近时间 / 分支按钮显示  不再右对齐 */}
        {(isStreaming && onCancel) || (!isStreaming && onRetry) || (!fullscreen && onFullscreen) ? (
          <Flexbox horizontal align="center" gap={4} className="reply-bubble-toolbar">
            {isStreaming && onCancel ? (
              <Tooltip title="终止该 agent 回答">
                <Button
                  aria-label="终止"
                  className="reply-bubble-action-button"
                  icon={<StopOutlined />}
                  onClick={onCancel}
                  size="small"
                  type="text"
                  shape="circle"
                />
              </Tooltip>
            ) : null}
            {!isStreaming && onRetry ? (
              <Tooltip title="重新回答">
                <Button
                  aria-label="重新回答"
                  className="reply-bubble-action-button"
                  icon={<ReloadOutlined />}
                  onClick={onRetry}
                  size="small"
                  type="text"
                  shape="circle"
                />
              </Tooltip>
            ) : null}
            {!fullscreen && onFullscreen ? (
              <Tooltip title="放大查看">
                <Button
                  aria-label="放大"
                  className="reply-bubble-action-button"
                  icon={<ExpandAltOutlined />}
                  onClick={onFullscreen}
                  size="small"
                  type="text"
                  shape="circle"
                />
              </Tooltip>
            ) : null}
          </Flexbox>
        ) : null}
      </Flexbox>
      {/* 正文 */}
      <Flexbox gap={8} style={{ maxWidth: '100%', width: '100%' }}>
        {body}
      </Flexbox>
    </Flexbox>
  );
}

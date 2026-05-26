// 输入区:文本框 + 发送/停止按钮(根据 task 状态切换)
// 这里改成更贴近 LobeHub 的输入卡外观，但发送、停止和占位逻辑保持不变
import { Button, Input, Popover, Progress, Tooltip } from 'antd';
import { PlusOutlined, SendOutlined, StopOutlined } from '@ant-design/icons';
import { useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { useChat } from '../state/ChatContext';
import { isBusyState } from '../state/types';
import type { ContextUsage, TaskState } from '../state/types';
import { useContextActions } from '../hooks/useContextActions';

// 单条消息最大字符数  超出禁止发送  防止用户一次塞超长文本撑爆 LLM 上下文
// 与后端摘要触发阈值无关  那个看 token  这里看 char  双层防线
const MAX_CHARS = 5000;
// 接近上限时提示色变化  超过 80% 变橙色提示用户控制长度
const WARN_THRESHOLD = Math.floor(MAX_CHARS * 0.8);

// token 数显示成 K  一万以下保留原值  避免 12345 这种小数读起来累
// > 1000 显示成 12.3K  方便扫读  保留一位小数
function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  if (n < 1000) return String(Math.round(n));
  return `${(n / 1000).toFixed(1)}K`;
}

// 根据 ratio 选择阶梯式颜色  跟 char 计数颜色风格一致
//   < 60%  绿
//   60% ~ 80%  黄
//   >= 80%  红
function pickUsageColor(ratio: number): string {
  if (ratio >= 0.8) return '#ef4444';
  if (ratio >= 0.6) return '#f59e0b';
  return '#10b981';
}

// 圆柱形容量指示器  内嵌 SVG 试管样式  液面高度跟 ratio 联动
// 整体尺寸 16x22  与 + 号 type=text 按钮视觉对齐  作为 Popover 触发器
interface ContextCylinderProps {
  ratio: number;
  color: string;
}
function ContextCylinder({ ratio, color }: ContextCylinderProps) {
  const cylTop = 3;
  const cylHeight = 16;
  // 液面从底部往上长  ratio=1 时填满 ratio=0 时高度为 0 完全空
  const fillH = Math.max(0, Math.min(cylHeight, cylHeight * ratio));
  const fillY = cylTop + (cylHeight - fillH);
  return (
    <svg
      width="14"
      height="22"
      viewBox="0 0 14 22"
      fill="none"
      style={{ display: 'block' }}
    >
      {/* 外管壁 */}
      <rect
        x="3"
        y={cylTop}
        width="8"
        height={cylHeight}
        rx="2"
        ry="2"
        stroke="#94a3b8"
        strokeWidth="1"
        fill="none"
      />
      {/* 液体填充  圆角与外壁一致避免视觉错位 */}
      <rect
        x="4"
        y={fillY}
        width="6"
        height={fillH}
        rx="1"
        ry="1"
        fill={color}
      />
    </svg>
  );
}

// 上下文用量浮窗内容  Popover 触发后展示
// 包含详细数字 + Progress 条 + 阈值说明 + 压缩按钮
interface ContextUsagePopoverContentProps {
  usage: ContextUsage;
  compacting: boolean;
  canCompact: boolean;
  onCompact: () => void;
}
function ContextUsagePopoverContent({
  usage,
  compacting,
  canCompact,
  onCompact,
}: ContextUsagePopoverContentProps) {
  const safeRatio = Math.max(0, Math.min(1, usage.ratio));
  const percent = Math.round(safeRatio * 1000) / 10;
  const color = pickUsageColor(safeRatio);
  return (
    <div style={{ minWidth: 240, maxWidth: 280 }}>
      <div
        style={{
          color: 'rgba(15, 23, 42, 0.92)',
          fontSize: 13,
          fontWeight: 600,
          marginBottom: 8,
        }}
      >
        上下文用量
      </div>
      <div
        style={{
          color,
          fontSize: 13,
          fontVariantNumeric: 'tabular-nums',
          marginBottom: 6,
        }}
      >
        {formatTokens(usage.used_tokens)} / {formatTokens(usage.max_input_tokens)} ({percent}%)
      </div>
      <Progress
        percent={Math.min(100, safeRatio * 100)}
        showInfo={false}
        size="small"
        strokeColor={color}
        style={{ marginBottom: 8 }}
      />
      <div
        style={{
          color: 'rgba(71, 85, 105, 0.72)',
          fontSize: 12,
          marginBottom: 12,
          lineHeight: 1.6,
        }}
      >
        模型 {usage.model_id || '未知'}  阈值 {formatTokens(usage.threshold_tokens)}
        <br />
        超过阈值后会自动摘要历史轮次
      </div>
      <Button
        block
        type="primary"
        size="small"
        loading={compacting}
        disabled={!canCompact || compacting}
        onClick={onCompact}
      >
        压缩上下文
      </Button>
    </div>
  );
}

// 大脑图标  作为 thinking 模式开关  active 时填色  inactive 时只描边
// 简化绘制  脑沟两条曲线 + 中线 + 外轮廓  16x16 统一吃 currentColor
interface BrainIconProps {
  active: boolean;
}
function BrainIcon({ active }: BrainIconProps) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      style={{ display: 'block' }}
    >
      <path
        d="M9.5 2A3.5 3.5 0 0 0 6 5.5v.06a3.5 3.5 0 0 0-2 6.39A3.5 3.5 0 0 0 6 18a3.5 3.5 0 0 0 6 1.5V4.06A3.5 3.5 0 0 0 9.5 2Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill={active ? 'currentColor' : 'none'}
        fillOpacity={active ? 0.18 : 0}
      />
      <path
        d="M14.5 2A3.5 3.5 0 0 1 18 5.5v.06a3.5 3.5 0 0 1 2 6.39A3.5 3.5 0 0 1 18 18a3.5 3.5 0 0 1-6 1.5V4.06A3.5 3.5 0 0 1 14.5 2Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill={active ? 'currentColor' : 'none'}
        fillOpacity={active ? 0.18 : 0}
      />
    </svg>
  );
}

export interface ChatInputProps {
  onSend: (message: string, options?: { thinking?: boolean }) => void | Promise<void>;
  onStop?: () => void | Promise<void>;
}

function getPlaceholder(state: TaskState): string {
  switch (state) {
    case 'THINKING':
      return '4 个 agent 正在思考';
    case 'THINK_DONE':
      return '等你选择回答的 agent';
    case 'DECIDED':
      return '已决策,等 agent 开始回答';
    case 'REPLYING':
      return 'agent 正在回答';
    default:
      return `从任何想法开始… 按 Enter 发送,Shift + Enter 换行  最多 ${MAX_CHARS} 字`;
  }
}

export default function ChatInput({ onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState('');
  const { state } = useChat();
  const { compact } = useContextActions();
  const busy = isBusyState(state.taskState);
  // compacting 期间统一冻结发送 / 停止 / 输入  避免半路打断后端长事务
  const compacting = state.compacting;
  const inputDisabled = busy || compacting;
  const allowStop = busy && state.activeTaskId !== null;
  const canCompact = !!state.sessionId;

  const charCount = value.length;
  const overLimit = charCount > MAX_CHARS;
  const nearLimit = charCount >= WARN_THRESHOLD;

  // 圆柱触发的 Popover 显示状态  受控  压缩中也允许查看
  // 压缩成功后 useContextActions 会刷新 contextUsage  浮窗里数据自动跟新
  const [usagePopoverOpen, setUsagePopoverOpen] = useState(false);

  // 深度思考开关  本地 state  不持久化  每次发送透传给 onSend
  // active = true 时调 /ask 会传 thinking:true 后端注入 extra_body 让模型走深度思考
  const [thinkingEnabled, setThinkingEnabled] = useState(false);

  const handleSend = () => {
    const nextValue = value.trim();
    if (!nextValue || busy || compacting) return;
    // 双保险  TextArea 自带 maxLength 已挡 这里再校一次防绕过
    if (nextValue.length > MAX_CHARS) return;
    setValue('');
    void onSend(nextValue, { thinking: thinkingEnabled });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

  // 压缩按钮点击  Popover 保持打开 让用户看到 loading 与结果
  const handleCompact = () => {
    void compact();
  };

  // 圆柱按钮  没有 contextUsage 时不渲染  保持底部排版整洁
  const cylinderButton = state.contextUsage ? (
    <Popover
      content={
        <ContextUsagePopoverContent
          usage={state.contextUsage}
          compacting={compacting}
          canCompact={canCompact}
          onCompact={handleCompact}
        />
      }
      trigger="click"
      placement="topLeft"
      open={usagePopoverOpen}
      onOpenChange={setUsagePopoverOpen}
    >
      <Tooltip
        title={
          state.contextUsage
            ? `已用上下文 ${formatTokens(state.contextUsage.used_tokens)} / ${formatTokens(state.contextUsage.max_input_tokens)}  点击查看`
            : ''
        }
      >
        <Button
          shape="circle"
          type="text"
          aria-label="查看上下文用量"
          icon={
            <ContextCylinder
              ratio={Math.max(0, Math.min(1, state.contextUsage.ratio))}
              color={pickUsageColor(
                Math.max(0, Math.min(1, state.contextUsage.ratio)),
              )}
            />
          }
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        />
      </Tooltip>
    </Popover>
  ) : null;

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 20,
        boxShadow: '0 12px 32px rgba(15, 23, 42, 0.06)',
        overflow: 'hidden',
        width: '100%',
      }}
    >
      <div style={{ minHeight: 88, padding: '14px 16px 6px' }}>
        <Input.TextArea
          autoSize={{ minRows: 2, maxRows: 8 }}
          disabled={inputDisabled}
          maxLength={MAX_CHARS}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            compacting ? '正在压缩上下文 请稍候' : getPlaceholder(state.taskState)
          }
          style={{
            background: 'transparent',
            border: 'none',
            boxShadow: 'none',
            fontSize: 14,
            lineHeight: 1.7,
            padding: 0,
            resize: 'none',
          }}
          value={value}
          variant="borderless"
        />
      </div>

      <div
        style={{
          alignItems: 'center',
          display: 'flex',
          gap: 8,
          justifyContent: 'space-between',
          padding: '4px 8px 8px 12px',
        }}
      >
        <div style={{ alignItems: 'center', display: 'flex', gap: 8, minWidth: 0 }}>
          <Tooltip title="添加文件、技能和更多上下文(占位)">
            <Button shape="circle" icon={<PlusOutlined />} type="text" disabled={compacting} />
          </Tooltip>
          <Tooltip title={thinkingEnabled ? '深度思考已开启 点击关闭' : '开启深度思考'}>
            <Button
              shape="circle"
              type="text"
              aria-label={thinkingEnabled ? '关闭深度思考' : '开启深度思考'}
              aria-pressed={thinkingEnabled}
              disabled={compacting}
              onClick={() => setThinkingEnabled((v) => !v)}
              icon={<BrainIcon active={thinkingEnabled} />}
              style={{
                color: thinkingEnabled ? '#2563eb' : 'rgba(71, 85, 105, 0.7)',
                background: thinkingEnabled ? 'rgba(37, 99, 235, 0.08)' : 'transparent',
              }}
            />
          </Tooltip>
          {cylinderButton}
          <span
            style={{
              color: overLimit
                ? '#ef4444'
                : nearLimit
                  ? '#f59e0b'
                  : 'rgba(71, 85, 105, 0.7)',
              fontSize: 12,
              fontVariantNumeric: 'tabular-nums',
              userSelect: 'none',
            }}
          >
            {charCount} / {MAX_CHARS}
          </span>
        </div>

        <div style={{ alignItems: 'center', display: 'flex', gap: 8, flexShrink: 0 }}>
          {allowStop ? (
            <Tooltip title={compacting ? '压缩中 暂不可停止' : '停止当前任务'}>
              <Button
                danger
                icon={<StopOutlined />}
                onClick={() => {
                  void onStop?.();
                }}
                shape="circle"
                size="large"
                disabled={!onStop || compacting}
              />
            </Tooltip>
          ) : (
            <Tooltip
              title={
                compacting
                  ? '压缩中 暂不可发送'
                  : overLimit
                    ? `超过 ${MAX_CHARS} 字 无法发送`
                    : '发送消息'
              }
            >
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSend}
                shape="circle"
                size="large"
                disabled={inputDisabled || !value.trim() || overLimit}
              />
            </Tooltip>
          )}
        </div>
      </div>
    </div>
  );
}

// 输入区:文本框 + 单/多 agent 切换 + 大脑深度思考开关 + 上下文用量胶囊 + 发送/停止
// LobeHub 风格输入卡  不发任何网络请求只触发 onSend 回调
import { Avatar, Button, Checkbox, Input, Popover, Progress, Tooltip } from 'antd';
import {
  CaretUpOutlined,
  PlusOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useEffect, useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { useChat } from '../state/ChatContext';
import { isBusyState } from '../state/types';
import type {
  AgentName,
  ContextUsage,
  InputMode,
  TaskState,
} from '../state/types';
import { useContextActions } from '../hooks/useContextActions';
import {
  agentAvatarOf,
  buildAgentLabelMap,
  buildAgentMetaMap,
} from '../state/agentLabels';

// 单条消息最大字符数  超出禁止发送
const MAX_CHARS = 5000;
const WARN_THRESHOLD = Math.floor(MAX_CHARS * 0.8);
// 多 agent 模式上限  一轮最多并发 4 个 agent
const MULTI_AGENT_LIMIT = 4;

function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  if (n < 1000) return String(Math.round(n));
  return `${(n / 1000).toFixed(1)}K`;
}

function pickUsageColor(ratio: number): string {
  if (ratio >= 0.8) return '#ef4444';
  if (ratio >= 0.6) return '#f59e0b';
  return '#10b981';
}

interface ContextCylinderProps {
  ratio: number;
  color: string;
}
function ContextCylinder({ ratio, color }: ContextCylinderProps) {
  const cylTop = 3;
  const cylHeight = 16;
  const fillH = Math.max(0, Math.min(cylHeight, cylHeight * ratio));
  const fillY = cylTop + (cylHeight - fillH);
  return (
    <svg width="14" height="22" viewBox="0 0 14 22" fill="none" style={{ display: 'block' }}>
      <rect x="3" y={cylTop} width="8" height={cylHeight} rx="2" ry="2" stroke="#94a3b8" strokeWidth="1" fill="none" />
      <rect x="4" y={fillY} width="6" height={fillH} rx="1" ry="1" fill={color} />
    </svg>
  );
}

interface ContextUsagePopoverContentProps {
  usage: ContextUsage;
  compacting: boolean;
  canCompact: boolean;
  onCompact: () => void;
}
function ContextUsagePopoverContent({ usage, compacting, canCompact, onCompact }: ContextUsagePopoverContentProps) {
  const safeRatio = Math.max(0, Math.min(1, usage.ratio));
  const percent = Math.round(safeRatio * 1000) / 10;
  const color = pickUsageColor(safeRatio);
  return (
    <div style={{ minWidth: 240, maxWidth: 280 }}>
      <div style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
        上下文用量
      </div>
      <div style={{ color, fontSize: 13, fontVariantNumeric: 'tabular-nums', marginBottom: 6 }}>
        {formatTokens(usage.used_tokens)} / {formatTokens(usage.max_input_tokens)} ({percent}%)
      </div>
      <Progress percent={Math.min(100, safeRatio * 100)} showInfo={false} size="small" strokeColor={color} style={{ marginBottom: 8 }} />
      <div style={{ color: 'rgba(71, 85, 105, 0.72)', fontSize: 12, marginBottom: 12, lineHeight: 1.6 }}>
        模型 {usage.model_id || '未知'}  阈值 {formatTokens(usage.threshold_tokens)}
        <br />
        超过阈值后会自动摘要历史轮次
      </div>
      <Button block type="primary" size="small" loading={compacting} disabled={!canCompact || compacting} onClick={onCompact}>
        压缩上下文
      </Button>
    </div>
  );
}

// 大脑图标  作为 thinking 模式开关
interface BrainIconProps {
  active: boolean;
}
function BrainIcon({ active }: BrainIconProps) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style={{ display: 'block' }}>
      <path d="M9.5 2A3.5 3.5 0 0 0 6 5.5v.06a3.5 3.5 0 0 0-2 6.39A3.5 3.5 0 0 0 6 18a3.5 3.5 0 0 0 6 1.5V4.06A3.5 3.5 0 0 0 9.5 2Z" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill={active ? 'currentColor' : 'none'} fillOpacity={active ? 0.18 : 0} />
      <path d="M14.5 2A3.5 3.5 0 0 1 18 5.5v.06a3.5 3.5 0 0 1 2 6.39A3.5 3.5 0 0 1 18 18a3.5 3.5 0 0 1-6 1.5V4.06A3.5 3.5 0 0 1 14.5 2Z" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" fill={active ? 'currentColor' : 'none'} fillOpacity={active ? 0.18 : 0} />
    </svg>
  );
}

// solo 图标  单人剪影  active 时填色
function SoloIcon({ active }: { active: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style={{ display: 'block' }}>
      <circle
        cx="12"
        cy="8"
        r="3.4"
        stroke="currentColor"
        strokeWidth="1.6"
        fill={active ? 'currentColor' : 'none'}
        fillOpacity={active ? 0.18 : 0}
      />
      <path
        d="M5 19.5c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        fill={active ? 'currentColor' : 'none'}
        fillOpacity={active ? 0.18 : 0}
      />
    </svg>
  );
}

// choice 图标  双人剪影  右人略后置 + 部分重叠  active 时填色
function ChoiceIcon({ active }: { active: boolean }) {
  return (
    <svg width="18" height="16" viewBox="0 0 26 24" fill="none" style={{ display: 'block' }}>
      {/* 后方人物  整体右上 略小 */}
      <circle
        cx="17"
        cy="7.5"
        r="3"
        stroke="currentColor"
        strokeWidth="1.5"
        fill={active ? 'currentColor' : 'none'}
        fillOpacity={active ? 0.18 : 0}
      />
      <path
        d="M11 19c0-3 2.7-5 6-5s6 2 6 5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        fill={active ? 'currentColor' : 'none'}
        fillOpacity={active ? 0.18 : 0}
      />
      {/* 前方人物  略大居左 */}
      <circle
        cx="9"
        cy="9"
        r="3.4"
        stroke="currentColor"
        strokeWidth="1.6"
        fill="#fff"
      />
      <circle
        cx="9"
        cy="9"
        r="3.4"
        stroke="currentColor"
        strokeWidth="1.6"
        fill={active ? 'currentColor' : '#fff'}
        fillOpacity={active ? 0.18 : 1}
      />
      <path
        d="M2 20.5c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        fill="#fff"
      />
    </svg>
  );
}

export interface ChatInputProps {
  onSend: (
    message: string,
    options: { thinking?: boolean; agents: AgentName[]; inputMode: InputMode },
  ) => void | Promise<void>;
  onStop?: () => void | Promise<void>;
  initialValue?: string;
  sendLabel?: string;
  editMode?: boolean;
  onCancelEdit?: () => void;
}

function getPlaceholder(state: TaskState): string {
  switch (state) {
    case 'PENDING':
      return '正在准备…';
    case 'REPLYING':
      return 'agent 正在回答';
    default:
      return `从任何想法开始… 按 Enter 发送,Shift + Enter 换行  最多 ${MAX_CHARS} 字`;
  }
}

// 多 agent 选择浮窗  agent 列表 + 上限提示
interface MultiAgentPopoverProps {
  agentNames: AgentName[];
  agentLabels: Record<string, string>;
  agentAvatars: Record<string, string | null>;
  selected: Set<AgentName>;
  onToggle: (name: AgentName) => void;
}
function MultiAgentPopover({
  agentNames,
  agentLabels,
  agentAvatars,
  selected,
  onToggle,
}: MultiAgentPopoverProps) {
  return (
    <div style={{ minWidth: 240, maxWidth: 320, maxHeight: 360, overflow: 'auto' }}>
      <div style={{ color: 'rgba(15, 23, 42, 0.86)', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
        选择回答的 agents (最多 {MULTI_AGENT_LIMIT} 个)
      </div>
      {agentNames.length === 0 ? (
        <div style={{ color: 'rgba(71, 85, 105, 0.7)', fontSize: 13, padding: '12px 0' }}>
          暂无 agent  请先在配置抽屉里新建
        </div>
      ) : null}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {agentNames.map((name) => {
          const checked = selected.has(name);
          const disabled = !checked && selected.size >= MULTI_AGENT_LIMIT;
          const label = agentLabels[name] ?? name;
          const avatarUrl = agentAvatars[name];
          return (
            <div
              key={name}
              onClick={() => {
                if (disabled) return;
                onToggle(name);
              }}
              style={{
                alignItems: 'center',
                cursor: disabled ? 'not-allowed' : 'pointer',
                display: 'flex',
                gap: 8,
                opacity: disabled ? 0.4 : 1,
                padding: '6px 8px',
                borderRadius: 8,
                background: checked ? 'rgba(37, 99, 235, 0.08)' : 'transparent',
              }}
            >
              <Checkbox checked={checked} disabled={disabled} />
              {avatarUrl ? (
                <Avatar src={avatarUrl} size={20} shape="square" />
              ) : (
                <Avatar size={20} shape="square" icon={<UserOutlined />} />
              )}
              <span style={{ flex: 1, fontSize: 13, color: 'rgba(15, 23, 42, 0.86)' }}>
                {label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 单 agent 选择浮窗  单选
interface SingleAgentPopoverProps {
  agentNames: AgentName[];
  agentLabels: Record<string, string>;
  agentAvatars: Record<string, string | null>;
  selected: AgentName | null;
  onPick: (name: AgentName) => void;
}
function SingleAgentPopover({
  agentNames,
  agentLabels,
  agentAvatars,
  selected,
  onPick,
}: SingleAgentPopoverProps) {
  return (
    <div style={{ minWidth: 220, maxWidth: 320, maxHeight: 360, overflow: 'auto' }}>
      <div style={{ color: 'rgba(15, 23, 42, 0.86)', fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
        选择回答的 agent
      </div>
      {agentNames.length === 0 ? (
        <div style={{ color: 'rgba(71, 85, 105, 0.7)', fontSize: 13, padding: '12px 0' }}>
          暂无 agent  请先在配置抽屉里新建
        </div>
      ) : null}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {agentNames.map((name) => {
          const active = selected === name;
          const label = agentLabels[name] ?? name;
          const avatarUrl = agentAvatars[name];
          return (
            <div
              key={name}
              onClick={() => onPick(name)}
              style={{
                alignItems: 'center',
                cursor: 'pointer',
                display: 'flex',
                gap: 8,
                padding: '6px 8px',
                borderRadius: 8,
                background: active ? 'rgba(37, 99, 235, 0.08)' : 'transparent',
              }}
            >
              {avatarUrl ? (
                <Avatar src={avatarUrl} size={20} shape="square" />
              ) : (
                <Avatar size={20} shape="square" icon={<UserOutlined />} />
              )}
              <span style={{ flex: 1, fontSize: 13, color: 'rgba(15, 23, 42, 0.86)' }}>
                {label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function ChatInput({
  onSend,
  onStop,
  initialValue,
  sendLabel,
  editMode = false,
  onCancelEdit,
}: ChatInputProps) {
  const [value, setValue] = useState('');
  const { state } = useChat();
  const { compact } = useContextActions();
  const busy = isBusyState(state.taskState);
  const compacting = state.compacting;
  const inputDisabled = busy || compacting;
  const allowStop = busy && state.activeTaskId !== null;
  const canCompact = !!state.sessionId;

  const charCount = value.length;
  const overLimit = charCount > MAX_CHARS;
  const nearLimit = charCount >= WARN_THRESHOLD;

  const [usagePopoverOpen, setUsagePopoverOpen] = useState(false);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);

  // 输入模式 + 选中 agents  本地状态  每轮独立切换
  // 默认走单 agent  agent 选用第一个 agent (judgeTarget 兜底)
  const [inputMode, setInputMode] = useState<InputMode>('single');
  const [selectedSingle, setSelectedSingle] = useState<AgentName | null>(null);
  const [selectedMulti, setSelectedMulti] = useState<Set<AgentName>>(new Set());
  const [singlePopoverOpen, setSinglePopoverOpen] = useState(false);
  const [multiPopoverOpen, setMultiPopoverOpen] = useState(false);

  useEffect(() => {
    setValue(initialValue ?? '');
  }, [initialValue]);

  // 从 settings.drafts 派生 agent 列表
  const agentLabels = buildAgentLabelMap(state.settings.drafts);
  const agentMetas = buildAgentMetaMap(state.settings.drafts);
  const agentNames = Object.keys(state.settings.drafts);
  const agentAvatarsMap: Record<string, string | null> = {};
  for (const n of agentNames) {
    agentAvatarsMap[n] = agentAvatarOf(agentMetas, n);
  }

  // 初始化默认选中  优先 judgeTarget  否则第一个 agent
  useEffect(() => {
    if (selectedSingle === null && agentNames.length > 0) {
      const fallback =
        state.settings.judgeTarget && agentNames.includes(state.settings.judgeTarget)
          ? state.settings.judgeTarget
          : agentNames[0];
      setSelectedSingle(fallback);
    }
  }, [agentNames.join('|'), state.settings.judgeTarget, selectedSingle]);

  const handleToggleMulti = (name: AgentName) => {
    setSelectedMulti((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        if (next.size >= MULTI_AGENT_LIMIT) return prev;
        next.add(name);
      }
      return next;
    });
  };

  const handleSend = () => {
    const nextValue = value.trim();
    if (!nextValue || busy || compacting) return;
    if (nextValue.length > MAX_CHARS) return;

    let agents: AgentName[] = [];
    if (inputMode === 'single') {
      if (!selectedSingle) return;
      agents = [selectedSingle];
    } else {
      agents = Array.from(selectedMulti);
      if (agents.length < 2) return;
    }
    setValue('');
    void onSend(nextValue, { thinking: thinkingEnabled, agents, inputMode });
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

  const handleCompact = () => {
    void compact();
  };

  // 当前已选 agent 的展示 chip 内容  用于触发器
  const renderAgentTrigger = () => {
    if (inputMode === 'single') {
      const name = selectedSingle ?? agentNames[0] ?? '';
      const label = agentLabels[name] ?? name ?? '选择 agent';
      const avatarUrl = name ? agentAvatarsMap[name] : null;
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          {avatarUrl ? (
            <Avatar src={avatarUrl} size={18} shape="square" />
          ) : (
            <Avatar size={18} shape="square" icon={<UserOutlined />} />
          )}
          <span style={{ color: 'rgba(15, 23, 42, 0.86)' }}>{label || '选择 agent'}</span>
          <CaretUpOutlined style={{ fontSize: 10, color: 'rgba(71, 85, 105, 0.7)' }} />
        </span>
      );
    }
    const count = selectedMulti.size;
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
        <span style={{ color: 'rgba(15, 23, 42, 0.86)' }}>
          多 agent {count > 0 ? `(${count})` : '(请选)'}
        </span>
        <CaretUpOutlined style={{ fontSize: 10, color: 'rgba(71, 85, 105, 0.7)' }} />
      </span>
    );
  };

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
              color={pickUsageColor(Math.max(0, Math.min(1, state.contextUsage.ratio)))}
            />
          }
          style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
        />
      </Tooltip>
    </Popover>
  ) : null;

  // agent 选择触发器  根据模式渲染 single 或 multi popover
  const agentTriggerNode = inputMode === 'single' ? (
    <Popover
      content={
        <SingleAgentPopover
          agentNames={agentNames}
          agentLabels={agentLabels}
          agentAvatars={agentAvatarsMap}
          selected={selectedSingle}
          onPick={(n) => {
            setSelectedSingle(n);
            setSinglePopoverOpen(false);
          }}
        />
      }
      trigger="click"
      placement="topLeft"
      open={singlePopoverOpen}
      onOpenChange={setSinglePopoverOpen}
    >
      <Button size="small" type="text" disabled={compacting}>
        {renderAgentTrigger()}
      </Button>
    </Popover>
  ) : (
    <Popover
      content={
        <MultiAgentPopover
          agentNames={agentNames}
          agentLabels={agentLabels}
          agentAvatars={agentAvatarsMap}
          selected={selectedMulti}
          onToggle={handleToggleMulti}
        />
      }
      trigger="click"
      placement="topLeft"
      open={multiPopoverOpen}
      onOpenChange={setMultiPopoverOpen}
    >
      <Button size="small" type="text" disabled={compacting}>
        {renderAgentTrigger()}
      </Button>
    </Popover>
  );

  // 模式切换  纯图标按钮组  solo (单人) / choice (双人)
  // 视觉跟大脑、添加按钮统一  灰色文字色 + active 时灰底
  // tooltip 用 solo / choice 替代中文  视觉更轻
  const modeToggle = (
    <div style={{ display: 'inline-flex', gap: 2 }}>
      <Tooltip title="solo">
        <Button
          shape="circle"
          type="text"
          aria-label="solo"
          aria-pressed={inputMode === 'single'}
          disabled={compacting}
          onClick={() => setInputMode('single')}
          icon={<SoloIcon active={inputMode === 'single'} />}
          style={{
            color:
              inputMode === 'single'
                ? 'rgba(15, 23, 42, 0.86)'
                : 'rgba(71, 85, 105, 0.7)',
            background:
              inputMode === 'single'
                ? 'rgba(15, 23, 42, 0.06)'
                : 'transparent',
          }}
        />
      </Tooltip>
      <Tooltip title="choice">
        <Button
          shape="circle"
          type="text"
          aria-label="choice"
          aria-pressed={inputMode === 'multi'}
          disabled={compacting}
          onClick={() => setInputMode('multi')}
          icon={<ChoiceIcon active={inputMode === 'multi'} />}
          style={{
            color:
              inputMode === 'multi'
                ? 'rgba(15, 23, 42, 0.86)'
                : 'rgba(71, 85, 105, 0.7)',
            background:
              inputMode === 'multi'
                ? 'rgba(15, 23, 42, 0.06)'
                : 'transparent',
          }}
        />
      </Tooltip>
    </div>
  );

  // 发送按钮 disabled 条件:  无文本 / 超长 / 忙 / 单模式无选中 / 多模式不足 2 个
  const sendDisabled =
    inputDisabled ||
    !value.trim() ||
    overLimit ||
    (inputMode === 'single' && !selectedSingle) ||
    (inputMode === 'multi' && selectedMulti.size < 2);

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
        <div style={{ alignItems: 'center', display: 'flex', gap: 8, minWidth: 0, flexWrap: 'wrap' }}>
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
          {modeToggle}
          {agentTriggerNode}
          {cylinderButton}
          <span
            style={{
              color: overLimit ? '#ef4444' : nearLimit ? '#f59e0b' : 'rgba(71, 85, 105, 0.7)',
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
                    : inputMode === 'multi' && selectedMulti.size < 2
                      ? '多 agent 模式至少选 2 个'
                      : sendLabel || '发送消息'
              }
            >
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSend}
                size="large"
                disabled={sendDisabled}
                shape={editMode ? 'round' : 'circle'}
              >
                {editMode ? sendLabel || '重新发送' : null}
              </Button>
            </Tooltip>
          )}
          {editMode ? (
            <Tooltip title="取消编辑">
              <Button onClick={onCancelEdit} size="large">
                取消
              </Button>
            </Tooltip>
          ) : null}
        </div>
      </div>
    </div>
  );
}

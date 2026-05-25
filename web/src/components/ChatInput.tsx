// 输入区:文本框 + 发送/停止按钮(根据 task 状态切换)
// 这里改成更贴近 LobeHub 的输入卡外观，但发送、停止和占位逻辑保持不变
import { Button, Input, Tooltip } from 'antd';
import { PlusOutlined, SendOutlined, StopOutlined } from '@ant-design/icons';
import { useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { useChat } from '../state/ChatContext';
import { isBusyState } from '../state/types';
import type { TaskState } from '../state/types';

export interface ChatInputProps {
  onSend: (message: string) => void | Promise<void>;
  onStop?: () => void | Promise<void>;
}

function getPlaceholder(state: TaskState): string {
  switch (state) {
    case 'THINKING':
      return '4 个 agent 正在思考';
    case 'THINK_DONE':
      return '等你选择回答的 agent';
    case 'DECIDED':
      return '已决策，等 agent 开始回答';
    case 'REPLYING':
      return 'agent 正在回答';
    default:
      return '从任何想法开始… 按 Enter 发送，Shift + Enter 换行';
  }
}

export default function ChatInput({ onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState('');
  const { state } = useChat();
  const busy = isBusyState(state.taskState);
  const allowStop = busy && state.activeTaskId !== null;

  const handleSend = () => {
    const nextValue = value.trim();
    if (!nextValue || busy) return;
    setValue('');
    void onSend(nextValue);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      handleSend();
    }
  };

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
          disabled={busy}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={getPlaceholder(state.taskState)}
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
            <Button shape="circle" icon={<PlusOutlined />} type="text" />
          </Tooltip>
        </div>

        <div style={{ alignItems: 'center', display: 'flex', gap: 8, flexShrink: 0 }}>
          {allowStop ? (
            <Tooltip title="停止当前任务">
              <Button
                danger
                icon={<StopOutlined />}
                onClick={() => {
                  void onStop?.();
                }}
                shape="circle"
                size="large"
                disabled={!onStop}
              />
            </Tooltip>
          ) : (
            <Tooltip title="发送消息">
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSend}
                shape="circle"
                size="large"
                disabled={busy || !value.trim()}
              />
            </Tooltip>
          )}
        </div>
      </div>
    </div>
  );
}

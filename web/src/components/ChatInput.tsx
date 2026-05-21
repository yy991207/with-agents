// 输入区:文本框 + 发送/停止按钮(根据 task 状态切换)
// 状态机:
//   THINKING / REPLYING / DECIDED / THINK_DONE → 输入禁用,显示停止按钮
//   PENDING / DONE / CANCELLED                 → 可输入并发送
// H3 改造:
//   1. 统一从 state/types 导入 isBusyState,避免重复定义
//   2. placeholder 按 taskState 给更精准的文案
import { Button, Input, Space, Tooltip } from 'antd';
import { SendOutlined, StopOutlined } from '@ant-design/icons';
import { useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { useChat } from '../state/ChatContext';
import { isBusyState } from '../state/types';
import type { TaskState } from '../state/types';

export interface ChatInputProps {
  onSend: (message: string) => void | Promise<void>;
  onStop?: () => void | Promise<void>;
}

// 按当前 taskState 选择输入框的 placeholder 文案
// busy 时给出更细粒度的提示,引导用户理解当前阶段
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
      return '输入问题,Enter 发送,Shift+Enter 换行';
  }
}

export default function ChatInput({ onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState('');
  const { state } = useChat();
  const busy = isBusyState(state.taskState);
  // 等用户决策时也算 busy,但允许打断
  const allowStop = busy && state.activeTaskId !== null;

  const handleSend = () => {
    const v = value.trim();
    if (!v || busy) return;
    setValue('');
    void onSend(v);
  };

  // Enter 发送,Shift+Enter 换行
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      style={{
        padding: 12,
        borderTop: '1px solid #e5e7eb',
        background: '#fff',
      }}
    >
      <Space.Compact style={{ width: '100%' }}>
        <Input.TextArea
          autoSize={{ minRows: 1, maxRows: 6 }}
          placeholder={getPlaceholder(state.taskState)}
          value={value}
          disabled={busy}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          // 让 textarea 看起来跟 Input 一致地嵌入 Compact 中
          style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0, resize: 'none' }}
        />
        {allowStop ? (
          <Tooltip title="停止当前任务">
            <Button
              danger
              icon={<StopOutlined />}
              onClick={() => {
                void onStop?.();
              }}
              disabled={!onStop}
            >
              停止
            </Button>
          </Tooltip>
        ) : (
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            disabled={busy || !value.trim()}
          >
            发送
          </Button>
        )}
      </Space.Compact>
    </div>
  );
}

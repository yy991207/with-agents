// 输入区:文本框 + 发送/停止按钮(根据 task 状态切换)
import { Button, Input, Space } from 'antd';
import { useState } from 'react';
import { useChat } from '../state/ChatContext';

export interface ChatInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
}

export default function ChatInput({ onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState('');
  const { state } = useChat();
  // 是否处于"任务正在跑"的状态
  const running = ['THINKING', 'REPLYING'].includes(state.taskState);

  const handleSend = () => {
    const v = value.trim();
    if (!v) return;
    onSend(v);
    setValue('');
  };

  return (
    <div style={{ padding: 12, borderTop: '1px solid #e5e7eb', background: '#fff' }}>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          placeholder="输入问题,Enter 发送"
          value={value}
          disabled={running}
          onChange={(e) => setValue(e.target.value)}
          onPressEnter={handleSend}
        />
        {running ? (
          <Button danger onClick={onStop} disabled={!onStop}>
            停止
          </Button>
        ) : (
          <Button type="primary" onClick={handleSend}>
            发送
          </Button>
        )}
      </Space.Compact>
    </div>
  );
}

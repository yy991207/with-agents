// 首页:仅展示居中的输入框  发送会强制开启一个新会话
// 推荐功能卡片已废弃  这里不再渲染  保留 props 兼容签名
import type { ReactNode } from 'react';
import { Flexbox } from 'react-layout-kit';
import type { RecommendCardDefinition } from './lobeData';

export interface LobeHomeViewProps {
  input: ReactNode;
  // 兼容旧调用  不再使用  保留避免上层改动
  recommendPage?: number;
  agentLabel?: string;
  onRotateRecommendations?: () => void;
  onAction?: (card: RecommendCardDefinition) => void;
}

export default function LobeHomeView({ input }: LobeHomeViewProps) {
  return (
    <Flexbox
      width={'100%'}
      height={'100%'}
      align="center"
      justify="center"
      style={{ overflowY: 'auto', padding: '32px 16px' }}
    >
      {/* 居中容器:输入框最大宽度 720  在垂直方向居中 */}
      <Flexbox gap={20} width={'min(720px, 100%)'} align="stretch">
        <div
          style={{
            color: 'rgba(15, 23, 42, 0.92)',
            fontSize: 22,
            fontWeight: 600,
            textAlign: 'center',
          }}
        >
          从任何想法开始
        </div>
        {input}
      </Flexbox>
    </Flexbox>
  );
}

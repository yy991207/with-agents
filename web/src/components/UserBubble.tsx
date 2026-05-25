// 用户消息气泡:右对齐,LobeChat ChatItem 风格的浅灰底气泡
// 无边框/无阴影,padding 适中,圆角与 LobeChat 的 colorFillTertiary 一致
import { Flexbox } from 'react-layout-kit';

export interface UserBubbleProps {
  content: string;
  cancelled?: boolean;
  cancelReason?: string;
}

export default function UserBubble({
  content,
  cancelled = false,
  cancelReason,
}: UserBubbleProps) {
  return (
    <Flexbox
      align="flex-end"
      className="lobe-chat-item-right"
      gap={6}
      paddingBlock={8}
      style={{ paddingInlineStart: 36 }}
    >
      {cancelled ? (
        <div
          style={{
            background: 'rgba(239, 68, 68, 0.08)',
            borderRadius: 999,
            color: 'rgba(185, 28, 28, 0.9)',
            fontSize: 12,
            padding: '2px 10px',
          }}
        >
          已取消{cancelReason ? `:${cancelReason}` : ''}
        </div>
      ) : null}
      <div
        style={{
          background: 'rgba(15, 23, 42, 0.06)',
          borderRadius: 16,
          color: 'rgba(15, 23, 42, 0.92)',
          fontSize: 14,
          lineHeight: 1.7,
          maxWidth: 720,
          padding: '8px 14px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {content}
      </div>
    </Flexbox>
  );
}

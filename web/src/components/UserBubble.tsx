// 用户消息气泡:右对齐,更贴近 LobeHub 的圆角卡片外观
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
    <div style={{ alignItems: 'flex-end', display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0' }}>
      {cancelled ? (
        <div
          style={{
            background: 'rgba(239, 68, 68, 0.08)',
            border: '1px solid rgba(239, 68, 68, 0.16)',
            borderRadius: 999,
            color: 'rgba(185, 28, 28, 0.9)',
            fontSize: 12,
            padding: '4px 10px',
          }}
        >
          已取消{cancelReason ? `：${cancelReason}` : ''}
        </div>
      ) : null}
      <div
        style={{
          background: 'linear-gradient(180deg, #f8fbff 0%, #edf5ff 100%)',
          border: '1px solid #dbeafe',
          borderRadius: '22px 22px 8px 22px',
          boxShadow: '0 10px 24px rgba(59, 130, 246, 0.08)',
          color: 'rgba(15, 23, 42, 0.92)',
          maxWidth: 720,
          padding: '14px 16px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {content}
      </div>
    </div>
  );
}

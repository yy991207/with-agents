// 用户消息气泡:右对齐,LobeChat ChatItem 风格的浅灰底气泡
// 无边框/无阴影,padding 适中,圆角与 LobeChat 的 colorFillTertiary 一致
// 气泡左侧显示发送时间 (HH:mm) 来自 round.createdAt 后端落库  没值不显示
import { Flexbox } from 'react-layout-kit';

export interface UserBubbleProps {
  content: string;
  cancelled?: boolean;
  cancelReason?: string;
  // ISO8601 字符串 来自后端 round.created_at  没值表示历史数据缺字段不渲染
  createdAt?: string;
}

// ISO 字符串 → 本地 HH:mm 显示  失败兜底空串避免渲染异常
// 兼容性: 如果后端字符串没带时区  按 UTC 解释  防止历史 mongo naive datetime 导致显示晚 8 小时
function formatTime(iso?: string): string {
  if (!iso) return '';
  // 末尾不是 Z 也不是 +HH:MM/-HH:MM 形态  补一个 Z 强制按 UTC 解析
  const hasTZ = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso);
  const d = new Date(hasTZ ? iso : iso + 'Z');
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export default function UserBubble({
  content,
  cancelled = false,
  cancelReason,
  createdAt,
}: UserBubbleProps) {
  const timeText = formatTime(createdAt);
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
      <Flexbox horizontal align="flex-end" gap={8}>
        {timeText ? (
          <span
            style={{
              color: 'rgba(71, 85, 105, 0.5)',
              flexShrink: 0,
              fontSize: 11,
              lineHeight: 1.7,
            }}
          >
            {timeText}
          </span>
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
    </Flexbox>
  );
}

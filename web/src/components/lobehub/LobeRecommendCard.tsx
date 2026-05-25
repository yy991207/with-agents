import { Button, Tag } from 'antd';
import { X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';

export interface LobeRecommendCardProps {
  title: string;
  description: string;
  tag: string;
  actionLabel: string;
  icon: LucideIcon;
  onAction: () => void;
  onDismiss?: () => void;
}

export default function LobeRecommendCard({
  title,
  description,
  tag,
  actionLabel,
  icon: Icon,
  onAction,
  onDismiss,
}: LobeRecommendCardProps) {
  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 16,
        boxShadow: '0 8px 24px rgba(15, 23, 42, 0.04)',
        cursor: 'pointer',
        overflow: 'hidden',
        padding: 12,
      }}
    >
      <Flexbox gap={12}>
        {/* 标题行：图标 + 标题 + dismiss */}
        <Flexbox horizontal align="center" justify="space-between" gap={16} width={'100%'}>
          <Flexbox horizontal align="center" gap={8} flex={1} style={{ minWidth: 0 }}>
            <Flexbox
              align="center"
              justify="center"
              width={28}
              height={28}
              style={{
                background: 'rgba(15, 23, 42, 0.06)',
                borderRadius: 10,
                color: 'rgba(51, 65, 85, 0.72)',
                flex: '0 0 auto',
              }}
            >
              <Icon size={16} />
            </Flexbox>
            <div
              style={{
                color: 'rgba(15, 23, 42, 0.92)',
                flex: 1,
                fontSize: 16,
                fontWeight: 600,
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {title}
            </div>
          </Flexbox>
          {onDismiss ? (
            <Button
              size="small"
              type="text"
              icon={<X size={14} />}
              onClick={(event) => {
                event.stopPropagation();
                onDismiss();
              }}
              style={{ borderRadius: 6, height: 24, minWidth: 24, padding: 0, width: 24 }}
            />
          ) : null}
        </Flexbox>

        {/* 分割线，和参考页面的 divider 一致 */}
        <div
          style={{
            borderTop: '1px dashed #e5e7eb',
            margin: 0,
          }}
        />

        {/* 描述走 article 标签对齐参考 HTML */}
        <article
          style={{
            color: 'rgba(30, 41, 59, 0.82)',
            fontSize: 14,
            lineHeight: 1.6,
            margin: 0,
            minHeight: 44,
          }}
        >
          {description}
        </article>

        {/* 底部：标签 + 操作按钮 */}
        <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'}>
          <Tag bordered={false} style={{ borderRadius: 999, margin: 0 }}>
            {tag}
          </Tag>
          <Button shape="round" onClick={onAction}>
            {actionLabel}
          </Button>
        </Flexbox>
      </Flexbox>
    </div>
  );
}

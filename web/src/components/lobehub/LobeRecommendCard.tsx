import { Button, Card, Tag } from 'antd';
import type { LucideIcon } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';

export interface LobeRecommendCardProps {
  title: string;
  description: string;
  tag: string;
  actionLabel: string;
  icon: LucideIcon;
  onAction: () => void;
}

export default function LobeRecommendCard({
  title,
  description,
  tag,
  actionLabel,
  icon: Icon,
  onAction,
}: LobeRecommendCardProps) {
  return (
    <Card
      hoverable
      bodyStyle={{ padding: 12 }}
      style={{
        borderRadius: 16,
        borderColor: '#e5e7eb',
        boxShadow: '0 8px 24px rgba(15, 23, 42, 0.04)',
      }}
    >
      <Flexbox gap={12}>
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
        </Flexbox>

        <div
          style={{
            color: 'rgba(51, 65, 85, 0.72)',
            fontSize: 14,
            lineHeight: 1.6,
            minHeight: 44,
          }}
        >
          {description}
        </div>

        <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'}>
          <Tag bordered={false} style={{ borderRadius: 999, margin: 0 }}>
            {tag}
          </Tag>
          <Button shape="round" onClick={onAction}>
            {actionLabel}
          </Button>
        </Flexbox>
      </Flexbox>
    </Card>
  );
}

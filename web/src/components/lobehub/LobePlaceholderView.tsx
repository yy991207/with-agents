import { Button, Card, Tag } from 'antd';
import { ArrowRight, Sparkles } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';
import type { WorkbenchView } from '../../state/types';
import { PLACEHOLDER_DEFINITIONS } from './lobeData';

export interface LobePlaceholderViewProps {
  view: Exclude<WorkbenchView, 'home' | 'chat'>;
  onGoHome: () => void;
  onOpenChat: () => void;
  onOpenSettings: () => void;
}

export default function LobePlaceholderView({
  view,
  onGoHome,
  onOpenChat,
  onOpenSettings,
}: LobePlaceholderViewProps) {
  const content = PLACEHOLDER_DEFINITIONS[view];

  return (
    <Flexbox
      align="center"
      justify="center"
      width={'100%'}
      height={'100%'}
      padding={24}
      className="lobe-placeholder-page"
    >
      <div style={{ width: 'min(960px, 100%)' }}>
        <Flexbox gap={20}>
          <Tag bordered={false} style={{ borderRadius: 999, margin: 0, width: 'fit-content' }}>
            {content.badge}
          </Tag>
          <div style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 28, fontWeight: 700, lineHeight: 1.25 }}>
            {content.title}
          </div>
          <div style={{ color: 'rgba(51, 65, 85, 0.72)', fontSize: 15, lineHeight: 1.8, maxWidth: 760 }}>
            {content.description}
          </div>
          <div
            style={{
              display: 'grid',
              gap: 12,
              gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            }}
          >
            {content.highlights.map((highlight) => (
              <Card key={highlight} bodyStyle={{ padding: 16 }} style={{ borderRadius: 16, borderColor: '#e5e7eb' }}>
                <Flexbox gap={10}>
                  <Sparkles size={18} color="rgba(51, 65, 85, 0.72)" />
                  <div style={{ color: 'rgba(30, 41, 59, 0.86)', fontSize: 14, lineHeight: 1.7 }}>
                    {highlight}
                  </div>
                </Flexbox>
              </Card>
            ))}
          </div>
          <Flexbox horizontal gap={8} wrap={'wrap'}>
            <Button type="primary" shape="round" onClick={onGoHome}>
              {content.primaryActionLabel}
            </Button>
            <Button shape="round" onClick={onOpenSettings}>
              {content.secondaryActionLabel}
            </Button>
            <Button type="text" icon={<ArrowRight size={14} />} onClick={onOpenChat}>
              打开聊天工作台
            </Button>
          </Flexbox>
        </Flexbox>
      </div>
    </Flexbox>
  );
}

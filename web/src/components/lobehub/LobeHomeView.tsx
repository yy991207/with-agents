import type { ReactNode } from 'react';
import { Button } from 'antd';
import { RefreshCw } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';
import {
  HOME_RECOMMEND_GROUPS,
  type RecommendCardDefinition,
} from './lobeData';
import LobeRecommendCard from './LobeRecommendCard';

export interface LobeHomeViewProps {
  input: ReactNode;
  recommendPage: number;
  agentLabel?: string;
  onRotateRecommendations: () => void;
  onAction: (card: RecommendCardDefinition) => void;
}

export default function LobeHomeView({
  input,
  recommendPage,
  onRotateRecommendations,
  onAction,
}: LobeHomeViewProps) {
  const cards = HOME_RECOMMEND_GROUPS[recommendPage % HOME_RECOMMEND_GROUPS.length] ?? HOME_RECOMMEND_GROUPS[0];

  return (
    <Flexbox width={'100%'} height={'100%'} style={{ overflowY: 'auto', padding: '44px 0 16vh' }}>
      <Flexbox width={'100%'} align="center">
        <Flexbox gap={40} width={'min(960px, 100%)'} style={{ paddingInline: 16 }}>
          <Flexbox gap={24}>
            {input}
          </Flexbox>

          <Flexbox gap={12}>
            <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'}>
              <div style={{ color: 'rgba(71, 85, 105, 0.72)', fontSize: 12 }}>
                为你推荐的一些功能
              </div>
              <Button type="text" size="small" icon={<RefreshCw size={12} />} onClick={onRotateRecommendations}>
                换一批
              </Button>
            </Flexbox>
            <div
              style={{
                display: 'grid',
                gap: 12,
                gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
              }}
            >
              {cards.map((card) => (
                <LobeRecommendCard
                  key={card.key}
                  title={card.title}
                  description={card.description}
                  tag={card.tag}
                  actionLabel={card.actionLabel}
                  icon={card.icon}
                  onAction={() => onAction(card)}
                />
              ))}
            </div>
          </Flexbox>
        </Flexbox>
      </Flexbox>
    </Flexbox>
  );
}

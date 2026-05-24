import type { ReactNode } from 'react';
import { Button, Tag } from 'antd';
import { ActionIcon, Avatar } from '@lobehub/ui';
import { Bot, ChevronDown, MessageCircle, RefreshCw } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';
import type { WorkbenchView } from '../../state/types';
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
  onOpenView: (view: WorkbenchView) => void;
}

export default function LobeHomeView({
  input,
  recommendPage,
  agentLabel,
  onRotateRecommendations,
  onAction,
  onOpenView,
}: LobeHomeViewProps) {
  const cards = HOME_RECOMMEND_GROUPS[recommendPage % HOME_RECOMMEND_GROUPS.length] ?? HOME_RECOMMEND_GROUPS[0];
  const assistantName = agentLabel || 'Lobe AI';

  return (
    <Flexbox width={'100%'} height={'100%'} style={{ overflowY: 'auto', padding: '44px 0 16vh' }}>
      <Flexbox width={'100%'} align="center">
        <Flexbox gap={40} width={'min(960px, 100%)'} style={{ paddingInline: 16 }}>
          <Flexbox gap={24}>
            <Flexbox gap={8}>
              <div role="button" tabIndex={0} style={{ width: 'fit-content' }}>
                <Flexbox horizontal align="center" gap={8} style={{ marginInlineStart: -4, padding: 4 }}>
                  <Avatar
                    shape="square"
                    size={32}
                    icon={<Bot size={16} />}
                    style={{ background: 'rgba(15, 23, 42, 0.92)', color: '#fff' }}
                  />
                  <div style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 16, fontWeight: 600 }}>
                    {assistantName}
                  </div>
                  <ActionIcon icon={ChevronDown} size={{ blockSize: 24, borderRadius: 6 }} />
                </Flexbox>
              </div>
              <div
                style={{
                  color: 'rgba(30, 41, 59, 0.82)',
                  fontSize: 16,
                  lineHeight: 1.6,
                  minHeight: '3.2em',
                  paddingInlineStart: 5,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {'系统在线，随时帮你\n灵感加载中'}
              </div>
            </Flexbox>

            <div
              style={{
                background: '#fff',
                border: '1px solid #e5e7eb',
                borderRadius: 16,
                padding: '12px 14px',
                boxShadow: '0 8px 24px rgba(15, 23, 42, 0.04)',
              }}
            >
              <Flexbox horizontal align="center" justify="space-between" gap={12} width={'100%'}>
                <Flexbox horizontal align="center" gap={8}>
                  <MessageCircle size={18} color="rgba(51, 65, 85, 0.72)" />
                  <span style={{ color: 'rgba(51, 65, 85, 0.8)', fontSize: 14 }}>
                    在当前工作台里，用多 agent、Judge、MCP 和 Skills 协同完成复杂问答。
                  </span>
                </Flexbox>
              </Flexbox>
            </div>

            {input}

            <Flexbox horizontal gap={8} wrap={'wrap'}>
              <Tag color="default" style={{ borderRadius: 999, margin: 0 }}>
                上新
              </Tag>
              <Button shape="round" onClick={() => onOpenView('chat')}>
                Claude 工作台
              </Button>
              <Button shape="round" onClick={() => onOpenView('image')}>
                生成工作台
              </Button>
              <Button shape="round" onClick={() => onOpenView('resource')}>
                资源工作台
              </Button>
            </Flexbox>
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

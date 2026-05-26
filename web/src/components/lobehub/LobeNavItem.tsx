import type { DragEvent, ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';

export interface LobeNavItemProps {
  icon: LucideIcon;
  // 可选自定义 icon  传了优先用这个  比如助理列表渲染头像
  iconNode?: ReactNode;
  label: string;
  active?: boolean;
  dimmed?: boolean;
  badge?: ReactNode;
  actions?: ReactNode;
  onClick?: () => void;
  draggable?: boolean;
  onDragStart?: (event: DragEvent<HTMLDivElement>) => void;
}

export default function LobeNavItem({
  icon: Icon,
  iconNode,
  label,
  active = false,
  dimmed = false,
  badge,
  actions,
  onClick,
  draggable = false,
  onDragStart,
}: LobeNavItemProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      draggable={draggable}
      onClick={onClick}
      onDragStart={onDragStart}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick?.();
        }
      }}
      style={{
        borderRadius: 10,
        cursor: 'pointer',
        padding: '4px 6px',
        transition: 'background-color 0.2s ease, color 0.2s ease',
        background: active ? 'rgba(15, 23, 42, 0.06)' : 'transparent',
      }}
    >
      <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'}>
        <Flexbox horizontal align="center" gap={8} flex={1} style={{ minWidth: 0, overflow: 'hidden' }}>
          <Flexbox
            align="center"
            justify="center"
            width={28}
            height={28}
            style={{
              borderRadius: 8,
              color: active ? 'rgba(15, 23, 42, 0.92)' : 'rgba(51, 65, 85, 0.72)',
              flex: '0 0 auto',
              overflow: 'hidden',
            }}
          >
            {iconNode ?? <Icon size={18} />}
          </Flexbox>
          <div
            style={{
              color: active ? 'rgba(15, 23, 42, 0.92)' : dimmed ? 'rgba(51, 65, 85, 0.52)' : 'rgba(51, 65, 85, 0.72)',
              flex: 1,
              fontSize: 14,
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </div>
        </Flexbox>
        {(badge || actions) && (
          <Flexbox
            horizontal
            align="center"
            gap={4}
            style={{ flex: '0 0 auto' }}
            onClick={(event) => event.stopPropagation()}
          >
            {badge}
            {actions}
          </Flexbox>
        )}
      </Flexbox>
    </div>
  );
}

import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { Flexbox } from 'react-layout-kit';

export interface LobeSectionListProps {
  title: string;
  expanded: boolean;
  actions?: ReactNode;
  children?: ReactNode;
  onToggle?: () => void;
}

export default function LobeSectionList({
  title,
  expanded,
  actions,
  children,
  onToggle,
}: LobeSectionListProps) {
  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onToggle?.();
          }
        }}
        style={{
          cursor: 'pointer',
          padding: '6px 8px 4px',
          width: '100%',
        }}
      >
        <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'}>
          <Flexbox horizontal align="center" gap={4} flex={1} style={{ minWidth: 0 }}>
            <div
              style={{
                color: 'rgba(15, 23, 42, 0.88)',
                fontSize: 12,
                fontWeight: 600,
                letterSpacing: 0.2,
              }}
            >
              {title}
            </div>
            <ChevronRight
              size={14}
              style={{
                color: 'rgba(71, 85, 105, 0.56)',
                transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                transition: 'transform 0.2s ease',
              }}
            />
          </Flexbox>
          {actions ? (
            <div onClick={(event) => event.stopPropagation()}>
              {actions}
            </div>
          ) : null}
        </Flexbox>
      </div>
      {expanded ? <div style={{ paddingTop: 2 }}>{children}</div> : null}
    </div>
  );
}

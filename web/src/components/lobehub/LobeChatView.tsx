import { useMemo, useState, type DragEvent, type ReactNode, type RefObject } from 'react';
import { Flexbox } from 'react-layout-kit';
import TransientScrollbar from '../TransientScrollbar';
import type { ChatMultiViewLayout } from '../../state/types';

export interface LobeChatViewProps {
  scrollRef: RefObject<HTMLDivElement>;
  timeline: ReactNode;
  input: ReactNode;
  followResetKey?: unknown;
  panes?: ReactNode[];
  paneResetKeys?: unknown[];
  paneResetPositions?: Array<'top' | 'bottom' | 'preserve'>;
  layout?: ChatMultiViewLayout;
  activePaneIndex?: number;
  onDropSession?: (sessionId: string) => void;
  onSelectPane?: (index: number) => void;
}

export default function LobeChatView({
  scrollRef,
  timeline,
  input,
  followResetKey,
  panes,
  paneResetKeys,
  paneResetPositions,
  layout = 'single',
  activePaneIndex = 0,
  onDropSession,
  onSelectPane,
}: LobeChatViewProps) {
  const paneNodes = (panes && panes.length > 0 ? panes : [timeline]).slice(0, 2);
  const isSplit = layout !== 'single' && paneNodes.length > 1;
  const isSplitHorizontal = layout === 'split-horizontal';
  const [splitRatio, setSplitRatio] = useState(0.5);

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!onDropSession) return;
    event.preventDefault();
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!onDropSession) return;
    event.preventDefault();
    const sessionId = event.dataTransfer.getData('application/x-session-id');
    if (!sessionId) return;
    onDropSession(sessionId);
  };

  const splitTemplate = useMemo(() => {
    const first = `${Math.round(splitRatio * 1000) / 10}%`;
    const second = `${Math.round((1 - splitRatio) * 1000) / 10}%`;
    if (isSplitHorizontal) {
      return {
        gridTemplateColumns: 'minmax(0, 1fr)',
        gridTemplateRows: `minmax(0, ${first}) 10px minmax(0, ${second})`,
      };
    }
    return {
      gridTemplateColumns: `minmax(0, ${first}) 10px minmax(0, ${second})`,
      gridTemplateRows: 'minmax(0, 1fr)',
    };
  }, [isSplitHorizontal, splitRatio]);

  const renderPane = (pane: ReactNode, index: number) => (
    <div
      key={`pane-${index}`}
      onClick={() => onSelectPane?.(index)}
      style={{
        background: '#fff',
        borderRadius: 16,
        boxShadow:
          index === activePaneIndex
            ? '0 0 0 2px rgba(37, 99, 235, 0.28) inset'
            : '0 0 0 1px rgba(15, 23, 42, 0.06) inset',
        height: '100%',
        minHeight: 0,
        minWidth: 0,
        overflow: 'hidden',
      }}
    >
      <TransientScrollbar
        followResetKey={paneResetKeys?.[index]}
        resetPosition={paneResetPositions?.[index] ?? 'top'}
        style={{
          height: '100%',
          minHeight: 0,
          minWidth: 0,
          overflow: 'auto',
          overscrollBehavior: 'contain',
          padding: 12,
        }}
      >
        <div style={{ minWidth: 0 }}>{pane}</div>
      </TransientScrollbar>
    </div>
  );

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const host = event.currentTarget.parentElement;
    if (!host) return;
    const rect = host.getBoundingClientRect();
    const pointerId = event.pointerId;
    event.currentTarget.setPointerCapture(pointerId);

    const handleMove = (moveEvent: PointerEvent) => {
      let nextRatio = splitRatio;
      if (isSplitHorizontal) {
        nextRatio = (moveEvent.clientY - rect.top) / rect.height;
      } else {
        nextRatio = (moveEvent.clientX - rect.left) / rect.width;
      }
      nextRatio = Math.max(0.25, Math.min(0.75, nextRatio));
      setSplitRatio(nextRatio);
    };

    const handleUp = () => {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
    };

    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
  };

  if (!isSplit) {
    return (
      <Flexbox width={'100%'} height={'100%'} style={{ minHeight: 0 }}>
        <TransientScrollbar
          ref={scrollRef}
          followResetKey={followResetKey}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 0 12vh' }}
        >
          <Flexbox width={'100%'} align="center">
            <div style={{ width: 'min(960px, 100%)', paddingInline: 16 }}>{timeline}</div>
          </Flexbox>
        </TransientScrollbar>
        <div style={{ padding: '0 16px 16px' }}>
          <div style={{ margin: '0 auto', width: 'min(960px, 100%)' }}>{input}</div>
        </div>
      </Flexbox>
    );
  }

  return (
    <Flexbox width={'100%'} height={'100%'} style={{ minHeight: 0 }}>
      <div
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        style={{ flex: 1, minHeight: 0, padding: '16px 16px 12px' }}
      >
        <div
          style={{
            display: 'grid',
            gap: 0,
            height: '100%',
            minHeight: 0,
            width: '100%',
            ...splitTemplate,
          }}
        >
          <div
            style={{
              gridColumn: isSplitHorizontal ? 1 : 1,
              gridRow: isSplitHorizontal ? 1 : 1,
              minHeight: 0,
              minWidth: 0,
            }}
          >
            {renderPane(paneNodes[0], 0)}
          </div>

          <div
            onPointerDown={startResize}
            style={{
              alignSelf: 'stretch',
              background: 'transparent',
              cursor: isSplitHorizontal ? 'row-resize' : 'col-resize',
              gridColumn: isSplitHorizontal ? 1 : 2,
              gridRow: isSplitHorizontal ? 2 : 1,
              position: 'relative',
            }}
          >
            <div
              style={{
                background: 'rgba(148, 163, 184, 0.32)',
                borderRadius: 999,
                height: isSplitHorizontal ? 2 : '100%',
                left: isSplitHorizontal ? 0 : 4,
                position: 'absolute',
                right: isSplitHorizontal ? 0 : 4,
                top: isSplitHorizontal ? 4 : 0,
              }}
            />
          </div>

          <div
            style={{
              gridColumn: isSplitHorizontal ? 1 : 3,
              gridRow: isSplitHorizontal ? 3 : 1,
              minHeight: 0,
              minWidth: 0,
            }}
          >
            {renderPane(paneNodes[1], 1)}
          </div>
        </div>
      </div>
      <div style={{ padding: '0 16px 16px' }}>
        <div style={{ margin: '0 auto', width: 'min(1280px, 100%)' }}>{input}</div>
      </div>
    </Flexbox>
  );
}

import type { ReactNode, RefObject } from 'react';
import { Flexbox } from 'react-layout-kit';
import TransientScrollbar from '../TransientScrollbar';

export interface LobeChatViewProps {
  scrollRef: RefObject<HTMLDivElement>;
  timeline: ReactNode;
  input: ReactNode;
}

export default function LobeChatView({
  scrollRef,
  timeline,
  input,
}: LobeChatViewProps) {
  return (
    <Flexbox width={'100%'} height={'100%'} style={{ minHeight: 0 }}>
      <TransientScrollbar
        ref={scrollRef}
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

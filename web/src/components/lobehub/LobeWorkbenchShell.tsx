import type { ReactNode } from 'react';
import { Flexbox } from 'react-layout-kit';

export interface LobeWorkbenchShellProps {
  sidebar: ReactNode;
  children: ReactNode;
}

export default function LobeWorkbenchShell({
  sidebar,
  children,
}: LobeWorkbenchShellProps) {
  return (
    <Flexbox horizontal width={'100%'} height={'100%'}>
      {sidebar}
      <Flexbox flex={1} width={'100%'} height={'100%'} padding={8} style={{ minWidth: 0 }}>
        <div className="workbench-main-shell">{children}</div>
      </Flexbox>
    </Flexbox>
  );
}

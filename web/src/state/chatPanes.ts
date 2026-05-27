import type { ChatMultiViewLayout, ChatViewPane } from './types';

export interface CloseChatPaneResult {
  panes: ChatViewPane[];
  layout: ChatMultiViewLayout;
  activePaneIndex: number;
  remainingSessionId: string | null;
}

// 关闭某个分屏 pane 后，统一收敛出新的 pane 列表和需要切换到的会话。
// 这里先按通用规则处理，当前业务实际最多只会有 2 个 pane。
export function closeChatPane(
  panes: ChatViewPane[],
  index: number,
  layout: ChatMultiViewLayout,
): CloseChatPaneResult | null {
  if (index < 0 || index >= panes.length) return null;
  if (panes.length <= 1) return null;

  const nextPanes = panes.filter((_, paneIndex) => paneIndex !== index);
  if (nextPanes.length === 0) return null;

  if (nextPanes.length === 1) {
    const [remainingPane] = nextPanes;
    return {
      panes: [{ key: 'primary', sessionId: remainingPane.sessionId }],
      layout: 'single',
      activePaneIndex: 0,
      remainingSessionId: remainingPane.sessionId,
    };
  }

  const nextActivePaneIndex = Math.min(index, nextPanes.length - 1);
  return {
    panes: nextPanes,
    layout,
    activePaneIndex: nextActivePaneIndex,
    remainingSessionId: nextPanes[nextActivePaneIndex]?.sessionId ?? null,
  };
}

import type { AgentEditDraft } from './types';

export type AgentLabelMap = Record<string, string>;

// 会话事件和历史 round 中必须保留内部 name，展示层再映射成用户可读显示名。
export function buildAgentLabelMap(
  drafts: Record<string, AgentEditDraft>,
): AgentLabelMap {
  const labels: AgentLabelMap = {};
  for (const [name, draft] of Object.entries(drafts)) {
    labels[name] = draft.displayName || name;
  }
  return labels;
}

export function agentLabelOf(
  labels: AgentLabelMap | undefined,
  name: string,
): string {
  return labels?.[name] || name;
}

import type { AgentEditDraft } from './types';

// 简单字符串 map  老接口保留兼容性
export type AgentLabelMap = Record<string, string>;

// 富信息 map  对话气泡 / 助理列表渲染时一次取齐
// 字段为 null 时调用方走兜底 显示首字母方块
export interface AgentMeta {
  displayName: string;
  avatarDataUrl: string | null;
}
export type AgentMetaMap = Record<string, AgentMeta>;

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

// 拿展示名 + 头像合一的富 map  渲染层用这个就够了
export function buildAgentMetaMap(
  drafts: Record<string, AgentEditDraft>,
): AgentMetaMap {
  const out: AgentMetaMap = {};
  for (const [name, draft] of Object.entries(drafts)) {
    out[name] = {
      displayName: draft.displayName || name,
      avatarDataUrl: draft.avatarDataUrl ?? null,
    };
  }
  return out;
}

export function agentLabelOf(
  labels: AgentLabelMap | undefined,
  name: string,
): string {
  return labels?.[name] || name;
}

export function agentAvatarOf(
  metas: AgentMetaMap | undefined,
  name: string,
): string | null {
  return metas?.[name]?.avatarDataUrl ?? null;
}

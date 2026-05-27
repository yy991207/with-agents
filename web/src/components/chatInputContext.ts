import type { AgentEditDraft, ContextUsage, InputMode } from '../state/types';

interface ResolveDisplayContextUsageArgs {
  usage: ContextUsage;
  inputMode: InputMode;
  selectedSingle: string | null;
  selectedMulti: Set<string>;
  drafts: Record<string, AgentEditDraft>;
}

function resolveAgentContextLimit(draft: AgentEditDraft | undefined): number | null {
  if (!draft) return null;
  const matched = draft.availableModels.find((item) => item.model_id === draft.model);
  if (!matched) return null;
  return matched.max_input_tokens > 0 ? matched.max_input_tokens : null;
}

export function resolveDisplayContextUsage({
  usage,
  inputMode,
  selectedSingle,
  selectedMulti,
  drafts,
}: ResolveDisplayContextUsageArgs): ContextUsage {
  const candidateLimits: number[] = [];

  if (inputMode === 'single') {
    const limit = resolveAgentContextLimit(
      selectedSingle ? drafts[selectedSingle] : undefined,
    );
    if (limit) candidateLimits.push(limit);
  } else {
    for (const agentName of selectedMulti) {
      const limit = resolveAgentContextLimit(drafts[agentName]);
      if (limit) candidateLimits.push(limit);
    }
  }

  if (candidateLimits.length === 0) {
    return usage;
  }

  const maxInputTokens = Math.min(...candidateLimits);
  const thresholdTokens = Math.floor(maxInputTokens * 0.8);
  const safeMax = maxInputTokens > 0 ? maxInputTokens : 1;

  return {
    ...usage,
    max_input_tokens: maxInputTokens,
    threshold_tokens: thresholdTokens,
    ratio: usage.used_tokens / safeMax,
  };
}

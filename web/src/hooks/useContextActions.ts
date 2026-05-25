// 会话上下文相关动作 hook  目前只暴露一键压缩
// 与 useChatTask 拆开是为了让 ChatInput 仅依赖最小动作集合  避免循环引用 / 重渲染
import { useCallback } from 'react';
import { message } from 'antd';
import { compactSession } from '../api/http';
import type { CompactResponse } from '../api/http';
import { useChat } from '../state/ChatContext';
import type { ContextUsage } from '../state/types';

// 把后端 compact 响应转成统一的 ContextUsage
// ratio 用 used_tokens_after / max_input_tokens 重新算  保留小数  UI 自己转百分比
function buildUsageFromCompact(
  resp: CompactResponse,
  prev: ContextUsage | null,
): ContextUsage {
  const max = resp.max_input_tokens > 0 ? resp.max_input_tokens : 1;
  // 阈值字段 compact 接口没回  尽量沿用上一次事件的值  没有就按 80% 兜底
  const threshold = prev?.threshold_tokens ?? Math.floor(max * 0.8);
  return {
    used_tokens: resp.used_tokens_after,
    threshold_tokens: threshold,
    max_input_tokens: max,
    ratio: resp.used_tokens_after / max,
    model_id: resp.model_id,
  };
}

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// 把 HTTP 错误码翻译成中文文案  按后端契约
//   404 session 不存在
//   409 有进行中 round
//   422 无可压缩 round
//   503 LLM 失败
function humanizeCompactError(raw: string): string {
  if (raw.includes('HTTP 404')) return '会话不存在 或已被清理';
  if (raw.includes('HTTP 409')) return '当前有进行中的任务  请等任务结束再试';
  if (raw.includes('HTTP 422')) return '当前没有可压缩的轮次';
  if (raw.includes('HTTP 503')) return '摘要生成失败  请稍后再试';
  return raw;
}

export interface UseContextActionsResult {
  // 触发一键压缩  内部已统一处理 dispatch + message 提示
  // 返回 Promise 仅用于调用方需要等待结束做后续动作  失败时不会 reject  统一吞掉
  compact: () => Promise<void>;
}

export function useContextActions(): UseContextActionsResult {
  const { state, dispatch } = useChat();

  const compact = useCallback(async (): Promise<void> => {
    const sessionId = state.sessionId;
    if (!sessionId) {
      message.warning('当前没有活跃会话  无法压缩');
      return;
    }
    if (state.compacting) {
      // 已经在压缩中  避免重复点击  按钮 loading 通常已经禁用
      return;
    }
    dispatch({ type: 'compact.start' });
    try {
      const resp = await compactSession(sessionId);
      const usage = buildUsageFromCompact(resp, state.contextUsage);
      dispatch({ type: 'compact.done', usage });
      // 成功提示  贴近后端语义  让用户看到 round 数和 token 变化
      message.success(
        `压缩完成  摘要至 ${resp.summary_until_round} 轮  上下文 ${resp.used_tokens_before} -> ${resp.used_tokens_after}`,
      );
    } catch (err) {
      dispatch({ type: 'compact.fail' });
      message.error(`压缩失败 ${humanizeCompactError(describeError(err))}`);
    }
  }, [dispatch, state.sessionId, state.compacting, state.contextUsage]);

  return { compact };
}

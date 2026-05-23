import type { RoundView } from './types';

// 抗刷新恢复时只允许真正未结束的任务重连 SSE。
// 如果历史已经显示 DONE/CANCELLED,说明后端内存 hub 大概率已释放,
// 再连 /sse/{taskId} 只会拿到 404,不能把已完成记录误改成取消。
export function findResumableTaskId(
  rounds: Pick<RoundView, 'taskId' | 'state'>[],
  activeTaskId: string | null,
): string | null {
  if (!activeTaskId) return null;
  const round = rounds.find((r) => r.taskId === activeTaskId);
  if (!round) return activeTaskId;
  if (round.state === 'DONE' || round.state === 'CANCELLED') return null;
  return activeTaskId;
}

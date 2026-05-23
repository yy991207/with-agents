// think 卡片折叠态(历史/非活跃轮):一个小条 点击展开 Modal 查看完整 think
// 数字员工模型重构后:agent 数量动态 用 round.thinks 的 keys 渲染
import { useState } from 'react';
import { Modal, Space, Tag, Typography } from 'antd';
import { getAgentColor } from '../theme/tokens';
import type { AgentLabelMap } from '../state/agentLabels';
import type { RoundView } from '../state/types';
import ThinkPanel from './ThinkPanel';

export interface ThinkCardChipProps {
  round: RoundView;
  agentLabels?: AgentLabelMap;
}

export default function ThinkCardChip({ round, agentLabels }: ThinkCardChipProps) {
  const [open, setOpen] = useState(false);

  const agents = Object.keys(round.thinks);

  // 简单统计:done / failed / cancelled / skipped 的数量
  const summary = agents.reduce(
    (acc, a) => {
      const t = round.thinks[a];
      if (!t) return acc;
      if (t.state === 'done') acc.done += 1;
      else if (t.state === 'failed') acc.failed += 1;
      else if (t.state === 'cancelled') acc.cancelled += 1;
      else if (t.state === 'skipped') acc.skipped += 1;
      return acc;
    },
    { done: 0, failed: 0, cancelled: 0, skipped: 0 },
  );

  return (
    <>
      <div
        onClick={() => setOpen(true)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '4px 10px',
          margin: '4px 0',
          borderRadius: 16,
          background: '#f0f2f5',
          cursor: 'pointer',
          fontSize: 12,
          color: 'rgba(0,0,0,0.65)',
        }}
      >
        <Typography.Text style={{ fontSize: 12 }}>
          {agents.length} 个 agent 的思考
        </Typography.Text>
        <Space size={4}>
          {agents.map((a) => (
            <span
              key={a}
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: getAgentColor(a),
                opacity: round.thinks[a]?.state === 'done' ? 1 : 0.35,
                display: 'inline-block',
              }}
            />
          ))}
        </Space>
        <Tag color="default" style={{ marginRight: 0 }}>
          {summary.done} 完成 · {summary.failed + summary.cancelled} 异常
        </Tag>
        <span style={{ color: 'rgba(0,0,0,0.35)' }}>展开</span>
      </div>
      <Modal
        title="本轮思考详情"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={960}
      >
        <ThinkPanel round={round} agentLabels={agentLabels} />
      </Modal>
    </>
  );
}

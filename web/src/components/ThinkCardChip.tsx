// think 卡片折叠态(历史/非活跃轮):一个小条 点击展开 Modal 查看完整 think
import { useState } from 'react';
import { Modal, Space, Tag, Typography } from 'antd';
import { ChevronRight } from 'lucide-react';
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

  const summary = agents.reduce(
    (accumulator, agent) => {
      const think = round.thinks[agent];
      if (!think) return accumulator;
      if (think.state === 'done') accumulator.done += 1;
      else if (think.state === 'failed') accumulator.failed += 1;
      else if (think.state === 'cancelled') accumulator.cancelled += 1;
      else if (think.state === 'skipped') accumulator.skipped += 1;
      return accumulator;
    },
    { done: 0, failed: 0, cancelled: 0, skipped: 0 },
  );

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpen(true);
          }
        }}
        style={{
          alignItems: 'center',
          background: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: 999,
          boxShadow: '0 8px 18px rgba(15, 23, 42, 0.04)',
          color: 'rgba(51, 65, 85, 0.72)',
          cursor: 'pointer',
          display: 'inline-flex',
          gap: 8,
          margin: '4px 0',
          padding: '6px 12px',
        }}
      >
        <Typography.Text style={{ fontSize: 12, margin: 0 }}>
          {agents.length} 个 agent 的思考
        </Typography.Text>
        <Space size={4}>
          {agents.map((agent) => (
            <span
              key={agent}
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: getAgentColor(agent),
                opacity: round.thinks[agent]?.state === 'done' ? 1 : 0.32,
                display: 'inline-block',
              }}
            />
          ))}
        </Space>
        <Tag color="default" style={{ borderRadius: 999, margin: 0 }}>
          {summary.done} 完成 · {summary.failed + summary.cancelled} 异常
        </Tag>
        <ChevronRight size={14} color="rgba(100, 116, 139, 0.56)" />
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

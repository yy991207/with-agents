// 选答 chips  本轮 replies 全部 done 之后展示在 grid 下方
// 用户点选某个 agent 头像 -> 调 /select_reply  下一轮 history 才能拼上
// 单 agent 模式不渲染该组件  后端已自动选答
import { Avatar, Tooltip } from 'antd';
import { CheckCircleFilled, UserOutlined } from '@ant-design/icons';
import { Flexbox } from 'react-layout-kit';
import { useChatTask } from '../hooks/useChatTask';
import { useChat } from '../state/ChatContext';
import {
  agentAvatarOf,
  agentLabelOf,
  buildAgentLabelMap,
  buildAgentMetaMap,
} from '../state/agentLabels';
import type { RoundView } from '../state/types';

export interface SelectReplyChipsProps {
  round: RoundView;
}

export default function SelectReplyChips({ round }: SelectReplyChipsProps) {
  const { state } = useChat();
  const { selectReplyAgent } = useChatTask();
  const agentLabels = buildAgentLabelMap(state.settings.drafts);
  const agentMetas = buildAgentMetaMap(state.settings.drafts);

  // 仅当 multi 模式 + 所有 reply 已终态(都 done/failed/cancelled) 时显示
  if (round.inputMode !== 'multi') return null;
  const replies = Object.values(round.replies);
  if (!replies.length) return null;
  const allFinal = replies.every(
    (r) => r.state === 'done' || r.state === 'failed' || r.state === 'cancelled',
  );
  if (!allFinal) return null;

  const handlePick = (agent: string) => {
    void selectReplyAgent(round.taskId, agent);
  };

  return (
    <Flexbox
      gap={8}
      style={{
        background: 'rgba(15, 23, 42, 0.04)',
        borderRadius: 14,
        marginTop: 12,
        padding: '12px 14px',
      }}
    >
      <span style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 13, fontWeight: 500 }}>
        {round.selectedReplyAgent
          ? '已选作正式回答  点击其它头像可切换'
          : '请选一个回答作为正式回答  仅选定后才能开启下一轮'}
      </span>
      <Flexbox horizontal gap={10} style={{ flexWrap: 'wrap' }}>
        {round.agents.map((name) => {
          const reply = round.replies[name];
          const label = agentLabelOf(agentLabels, name);
          const avatarUrl = agentAvatarOf(agentMetas, name);
          const selected = round.selectedReplyAgent === name;
          const disabled = !reply || reply.state !== 'done';
          return (
            <Tooltip
              key={name}
              title={
                disabled
                  ? `${label} 回答未完成  无法选中`
                  : selected
                    ? `${label} 已选定`
                    : `把 ${label} 的回答作为正式回答`
              }
            >
              <div
                onClick={() => {
                  if (disabled) return;
                  handlePick(name);
                }}
                style={{
                  alignItems: 'center',
                  background: selected ? 'rgba(37, 99, 235, 0.12)' : '#fff',
                  border: selected
                    ? '1px solid rgba(37, 99, 235, 0.7)'
                    : '1px solid rgba(15, 23, 42, 0.08)',
                  borderRadius: 999,
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  display: 'inline-flex',
                  gap: 6,
                  opacity: disabled ? 0.45 : 1,
                  padding: '4px 10px 4px 4px',
                  transition: 'all 120ms ease',
                }}
              >
                {avatarUrl ? (
                  <Avatar src={avatarUrl} size={22} shape="square" />
                ) : (
                  <Avatar size={22} shape="square" icon={<UserOutlined />} />
                )}
                <span style={{ color: 'rgba(15, 23, 42, 0.86)', fontSize: 12 }}>
                  {label}
                </span>
                {selected ? (
                  <CheckCircleFilled style={{ color: '#2563eb', fontSize: 14 }} />
                ) : null}
              </div>
            </Tooltip>
          );
        })}
      </Flexbox>
    </Flexbox>
  );
}

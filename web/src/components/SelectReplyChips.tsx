// 选答 chips  本轮 replies 全部 done 之后展示在 grid 下方
// 用户点选某个 agent 头像 -> 调 /select_reply  下一轮 history 才能拼上
// 单 agent 模式不渲染该组件  后端已自动选答
//
// 视觉风格 (用户要求):
//   - 不要蓝色主题色  全用深灰浅灰
//   - 选中态不加描边 / 不加背景  仅在头像旁边显示一个灰色圆边对号
//   - 未选中态视觉中性  只是 chip 形状  鼠标悬停略微变深
//   - 不再渲染中文说明文字  视觉更克制
//
// 锁定 (locked):
//   - 当下一轮已经发起后  本轮选答不可再改  避免历史拼接被回溯
//   - 视觉上  整组 chips 鼠标 cursor 改为 not-allowed  点击无效
import { Avatar, Tooltip } from 'antd';
import { CheckOutlined, UserOutlined } from '@ant-design/icons';
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
  // 是否锁定  下一轮已发起后传 true  此时 chips 不可点击
  locked?: boolean;
}

// 灰色圆边对号  仅选中态显示  视觉与 chip 一致克制
function SelectedBadge() {
  return (
    <span
      aria-label="已选定"
      style={{
        alignItems: 'center',
        background: '#fff',
        border: '1px solid rgba(15, 23, 42, 0.36)',
        borderRadius: '50%',
        color: 'rgba(15, 23, 42, 0.7)',
        display: 'inline-flex',
        height: 14,
        justifyContent: 'center',
        width: 14,
      }}
    >
      <CheckOutlined style={{ fontSize: 9 }} />
    </span>
  );
}

export default function SelectReplyChips({ round, locked = false }: SelectReplyChipsProps) {
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
    if (locked) return;
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
      <Flexbox horizontal gap={10} style={{ flexWrap: 'wrap' }}>
        {round.agents.map((name) => {
          const reply = round.replies[name];
          const label = agentLabelOf(agentLabels, name);
          const avatarUrl = agentAvatarOf(agentMetas, name);
          const selected = round.selectedReplyAgent === name;
          const replyDisabled = !reply || reply.state !== 'done';
          const disabled = replyDisabled || locked;
          const tooltipText = locked
            ? selected
              ? `${label} 已选定  本轮已锁定`
              : '本轮已锁定  下一轮已发起  无法切换选答'
            : replyDisabled
              ? `${label} 回答未完成  无法选中`
              : selected
                ? `${label} 已选定`
                : `把 ${label} 的回答作为正式回答`;
          return (
            <Tooltip key={name} title={tooltipText}>
              <div
                onClick={() => {
                  if (disabled) return;
                  handlePick(name);
                }}
                style={{
                  alignItems: 'center',
                  background: '#fff',
                  border: '1px solid rgba(15, 23, 42, 0.08)',
                  borderRadius: 999,
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  display: 'inline-flex',
                  gap: 6,
                  opacity: replyDisabled ? 0.45 : 1,
                  padding: '4px 10px 4px 4px',
                  transition: 'background 120ms ease',
                }}
                onMouseEnter={(e) => {
                  if (disabled || selected) return;
                  (e.currentTarget as HTMLDivElement).style.background =
                    'rgba(15, 23, 42, 0.06)';
                }}
                onMouseLeave={(e) => {
                  if (disabled || selected) return;
                  (e.currentTarget as HTMLDivElement).style.background = '#fff';
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
                {selected ? <SelectedBadge /> : null}
              </div>
            </Tooltip>
          );
        })}
      </Flexbox>
    </Flexbox>
  );
}

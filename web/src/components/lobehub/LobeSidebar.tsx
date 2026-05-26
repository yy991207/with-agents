import { message, Popconfirm } from 'antd';
import { ActionIcon } from '@lobehub/ui';
import {
  Bell,
  Bot,
  ChevronRight,
  Ellipsis,
  GitBranch,
  Hash,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';
import { Flexbox } from 'react-layout-kit';
import { deleteSession } from '../../api/http';
import { useSession } from '../../hooks/useSession';
import { useSettings } from '../../hooks/useSettings';
import { useChat } from '../../state/ChatContext';
import type { SessionMeta, WorkbenchView } from '../../state/types';
import { PRIMARY_NAV_ITEMS } from './lobeData';
import LobeNavItem from './LobeNavItem';
import LobeSectionList from './LobeSectionList';

export interface LobeSidebarProps {
  onNavigate: (view: WorkbenchView) => void;
  onOpenSettings: () => void | Promise<void>;
}

function describeError(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

export default function LobeSidebar({ onNavigate, onOpenSettings }: LobeSidebarProps) {
  const { state, dispatch } = useChat();
  const { sessions, sessionId, switchSession, refreshSessions } = useSession();
  const { switchTab } = useSettings();
  const [expandedSessionIds, setExpandedSessionIds] = useState<Record<string, boolean>>({});

  const drafts = Object.values(state.settings.drafts);

  const handleDeleteSession = async (targetSessionId: string): Promise<void> => {
    try {
      await deleteSession(targetSessionId);
      dispatch({ type: 'session.deleted', sessionId: targetSessionId });
      await refreshSessions();
      message.success('已删除该会话');
    } catch (error) {
      const detail = describeError(error);
      if (detail.includes('409')) {
        message.warning('该会话还有进行中的对话，请先取消或等其完成');
      } else if (detail.includes('404')) {
        dispatch({ type: 'session.deleted', sessionId: targetSessionId });
        await refreshSessions();
        message.info('该会话已不存在，已从列表中移除');
      } else {
        message.error(`删除会话失败：${detail}`);
      }
    }
  };

  const handleOpenAgent = (agentName: string): void => {
    switchTab(agentName);
    void onOpenSettings();
  };

  const collapsed = state.workbench.sidebarCollapsed;

  const handleToggleSidebar = (): void => {
    dispatch({ type: 'ui.sidebar.toggle' });
  };

  const toggleSessionExpand = (targetSessionId: string): void => {
    setExpandedSessionIds((prev) => ({
      ...prev,
      [targetSessionId]: !prev[targetSessionId],
    }));
  };

  const childrenMap = new Map<string | null, SessionMeta[]>();
  for (const session of sessions) {
    const key = session.parentSessionId ?? null;
    const bucket = childrenMap.get(key) ?? [];
    bucket.push(session);
    childrenMap.set(key, bucket);
  }
  const rootSessions = childrenMap.get(null) ?? [];

  const hasActiveDescendant = (targetSessionId: string): boolean => {
    const children = childrenMap.get(targetSessionId) ?? [];
    for (const child of children) {
      if (child.sessionId === sessionId) return true;
      if (hasActiveDescendant(child.sessionId)) return true;
    }
    return false;
  };

  const renderSessionNode = (session: SessionMeta, depth: number) => {
    const children = childrenMap.get(session.sessionId) ?? [];
    const hasChildren = children.length > 0;
    const expanded =
      expandedSessionIds[session.sessionId] ??
      (session.sessionId === sessionId || hasActiveDescendant(session.sessionId));
    return (
      <Flexbox gap={2} key={session.sessionId}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, paddingLeft: depth * 14 }}>
          {hasChildren ? (
            <button
              type="button"
              aria-label={expanded ? '收起子会话' : '展开子会话'}
              onClick={() => toggleSessionExpand(session.sessionId)}
              style={{
                alignItems: 'center',
                background: 'transparent',
                border: 'none',
                color: 'rgba(71, 85, 105, 0.62)',
                cursor: 'pointer',
                display: 'inline-flex',
                height: 18,
                justifyContent: 'center',
                padding: 0,
                width: 18,
              }}
            >
              <ChevronRight
                size={14}
                style={{
                  transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                  transition: 'transform 0.2s ease',
                }}
              />
            </button>
          ) : (
            <span style={{ display: 'inline-block', width: 18 }} />
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <LobeNavItem
              icon={session.parentSessionId ? GitBranch : Hash}
              label={session.title || '未命名'}
              active={session.sessionId === sessionId}
              actions={
                <Popconfirm
                  title="确认删除该会话"
                  description="将一并删除所有对话内容,不可恢复"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  cancelText="取消"
                  onConfirm={() => {
                    void handleDeleteSession(session.sessionId);
                  }}
                >
                  <ActionIcon icon={Trash2} title="删除会话" />
                </Popconfirm>
              }
              onClick={() => {
                void switchSession(session.sessionId);
                onNavigate('chat');
              }}
            />
          </div>
        </div>
        {hasChildren && expanded ? children.map((child) => renderSessionNode(child, depth + 1)) : null}
      </Flexbox>
    );
  };

  // 折叠态: 整列收成 ~48px 细窄条  自上而下展示:
  //   1) 展开按钮  2) 顶部主导航(PRIMARY_NAV_ITEMS) 图标  3) 最近第一条会话图标
  //   4) 全部 agent 头像  5) 设置按钮置底
  // 设计取舍:
  //   - 会话只展示第 1 条  避免列表过长撑爆侧栏  与展开态视觉信息密度对齐
  //   - 智能体列表全展示  数量本身就受限于用户配置  不容易撑爆
  //   - 选中态用 LobeNavItem 已有的 active 样式  保持折叠 / 展开切换时视觉一致
  if (collapsed) {
    const firstSession = sessions[0];
    return (
      <Flexbox
        align="center"
        gap={8}
        height={'100%'}
        padding={'12px 4px'}
        style={{
          background: 'var(--ant-color-bg-layout)',
          flex: '0 0 auto',
        }}
        width={48}
      >
        <ActionIcon
          icon={PanelLeftOpen}
          title="展开侧栏"
          onClick={handleToggleSidebar}
        />

        {/* 顶部主导航  跟展开态 PRIMARY_NAV_ITEMS 保持一致 */}
        <Flexbox align="center" gap={4} style={{ width: '100%' }}>
          {PRIMARY_NAV_ITEMS.map((item) => (
            <ActionIcon
              key={item.key}
              icon={item.icon}
              title={item.label}
              active={state.workbench.activeView === item.key}
              onClick={() => onNavigate(item.key)}
            />
          ))}
        </Flexbox>

        {/* 最近会话  仅第 1 条 */}
        {firstSession ? (
          <ActionIcon
            icon={Hash}
            title={firstSession.title || '未命名会话'}
            active={firstSession.sessionId === sessionId}
            onClick={() => {
              void switchSession(firstSession.sessionId);
              onNavigate('chat');
            }}
          />
        ) : null}

        {/* 智能体列表  全部展示 头像优先  否则 Bot 图标兜底 */}
        <Flexbox
          align="center"
          gap={4}
          flex={1}
          style={{ minHeight: 0, overflowY: 'auto', width: '100%' }}
        >
          {drafts.map((draft) =>
            draft.avatarDataUrl ? (
              <button
                key={draft.name}
                type="button"
                aria-label={draft.displayName || draft.name}
                title={draft.displayName || draft.name}
                onClick={() => handleOpenAgent(draft.name)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  height: 28,
                  padding: 0,
                  width: 28,
                }}
              >
                <img
                  src={draft.avatarDataUrl}
                  alt={draft.displayName || draft.name}
                  style={{
                    borderRadius: 8,
                    height: 28,
                    objectFit: 'cover',
                    width: 28,
                  }}
                />
              </button>
            ) : (
              <ActionIcon
                key={draft.name}
                icon={Bot}
                title={draft.displayName || draft.name}
                onClick={() => handleOpenAgent(draft.name)}
              />
            ),
          )}
        </Flexbox>

        {/* 设置入口置底  对齐展开态 Bell 位置 */}
        <ActionIcon icon={Bell} title="打开设置" onClick={() => void onOpenSettings()} />
      </Flexbox>
    );
  }

  return (
    <Flexbox
      height={'100%'}
      style={{
        background: 'var(--ant-color-bg-layout)',
        flex: '0 0 auto',
      }}
      width={320}
    >
      <Flexbox gap={8} height={'100%'} style={{ overflow: 'hidden', padding: 8 }}>
        <Flexbox gap={8}>
          <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'} padding={'0 2px'}>
            <Flexbox horizontal align="center" gap={8} style={{ minWidth: 0, overflow: 'hidden' }}>
              <Flexbox horizontal align="center" gap={4} style={{ minWidth: 0, overflow: 'hidden' }}>
                <div
                  style={{
                    color: 'rgba(15, 23, 42, 0.92)',
                    flex: 1,
                    fontSize: 14,
                    fontWeight: 600,
                    minWidth: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  With agents
                </div>
              </Flexbox>
            </Flexbox>
            <Flexbox horizontal gap={4}>
              <ActionIcon
                icon={PanelLeftClose}
                title="收起侧栏"
                onClick={handleToggleSidebar}
              />
              <ActionIcon icon={Bell} title="打开设置" onClick={() => void onOpenSettings()} />
            </Flexbox>
          </Flexbox>

          <Flexbox gap={2}>
            {PRIMARY_NAV_ITEMS.map((item) => (
              <LobeNavItem
                key={item.key}
                icon={item.icon}
                label={item.label}
                active={state.workbench.activeView === item.key}
                onClick={() => onNavigate(item.key)}
              />
            ))}
          </Flexbox>
        </Flexbox>

        <Flexbox flex={1} gap={8} style={{ minHeight: 0, overflowY: 'auto' }}>
          <LobeSectionList
            title="最近"
            expanded={state.workbench.recentExpanded}
            onToggle={() => dispatch({ type: 'ui.section.toggle', section: 'recent' })}
          >
            <Flexbox gap={2}>
              {rootSessions.map((session) => renderSessionNode(session, 0))}
              {sessions.length === 0 ? (
                <div style={{ color: 'rgba(71, 85, 105, 0.56)', fontSize: 12, padding: '4px 12px 8px' }}>
                  暂无会话
                </div>
              ) : null}
            </Flexbox>
          </LobeSectionList>

          <LobeSectionList
            title="助理"
            expanded={state.workbench.agentsExpanded}
            actions={<ActionIcon icon={Plus} title="创建助理" onClick={() => void onOpenSettings()} />}
            onToggle={() => dispatch({ type: 'ui.section.toggle', section: 'agents' })}
          >
            <Flexbox gap={2}>
              {drafts.map((draft) => (
                <LobeNavItem
                  key={draft.name}
                  icon={Bot}
                  iconNode={
                    draft.avatarDataUrl ? (
                      <img
                        src={draft.avatarDataUrl}
                        alt={draft.displayName || draft.name}
                        style={{
                          borderRadius: 8,
                          height: 24,
                          objectFit: 'cover',
                          width: 24,
                        }}
                      />
                    ) : undefined
                  }
                  label={draft.displayName || draft.name}
                  onClick={() => handleOpenAgent(draft.name)}
                  actions={<ActionIcon icon={Ellipsis} title="打开助理设置" onClick={() => handleOpenAgent(draft.name)} />}
                />
              ))}
            </Flexbox>
          </LobeSectionList>
        </Flexbox>
      </Flexbox>
    </Flexbox>
  );
}

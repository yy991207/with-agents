// 侧边栏:会话列表
// 支持多选 + 批量删除
import { useState } from 'react';
import { Button, Checkbox, List, message, Popconfirm } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { batchDeleteSessions, deleteSession } from '../api/http';
import { useSession } from '../hooks/useSession';
import { useChat } from '../state/ChatContext';

// 提取错误信息文本兜底
function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function SessionDrawer() {
  const { sessions, sessionId, switchSession, refreshSessions } = useSession();
  const { dispatch } = useChat();

  // 多选状态
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const toggleSelect = (sid: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) {
        next.delete(sid);
      } else {
        next.add(sid);
      }
      return next;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  // 删除单个会话
  const handleDelete = async (sid: string): Promise<void> => {
    try {
      await deleteSession(sid);
      dispatch({ type: 'session.deleted', sessionId: sid });
      message.success('已删除该会话');
    } catch (err) {
      const msg = describeError(err);
      if (msg.includes('409')) {
        message.warning('该会话还有进行中的对话 请先取消或等其完成');
      } else if (msg.includes('404')) {
        dispatch({ type: 'session.deleted', sessionId: sid });
        message.info('该会话已不存在 已从列表中移除');
      } else {
        message.error(`删除失败 ${msg}`);
      }
    }
  };

  // 批量删除
  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    try {
      const result = await batchDeleteSessions(ids);
      // 逐条清理前端状态
      for (const sid of ids) {
        dispatch({ type: 'session.deleted', sessionId: sid });
      }
      clearSelection();
      if (result.errors.length > 0) {
        message.warning(`删除完成，${result.deleted} 条成功，${result.skipped} 条跳过。错误:${result.errors.join(';')}`);
      } else {
        message.success(`已批量删除 ${result.deleted} 条会话`);
      }
      // 列表可能变动，刷新一下
      await refreshSessions();
    } catch (err) {
      message.error(`批量删除失败:${describeError(err)}`);
    }
  };

  // 全选 / 全不选
  const allIds = sessions.map((s) => s.sessionId);
  const allSelected = allIds.length > 0 && allIds.every((id) => selectedIds.has(id));
  const handleToggleAll = () => {
    if (allSelected) {
      clearSelection();
    } else {
      setSelectedIds(new Set(allIds));
    }
  };

  return (
    <div style={{ padding: 12, height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 顶部操作栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <strong>会话</strong>
        <div style={{ display: 'flex', gap: 4 }}>
          {selectedIds.size > 0 && (
            <Popconfirm
              title="确认批量删除"
              description={`将删除选中的 ${selectedIds.size} 个会话 不可恢复`}
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => { void handleBatchDelete(); }}
            >
              <Button danger size="small" icon={<DeleteOutlined />}>
                删除 ({selectedIds.size})
              </Button>
            </Popconfirm>
          )}
          <Button size="small" onClick={() => { void switchSession(null); }}>
            新会话
          </Button>
        </div>
      </div>

      {/* 全选 checkbox */}
      {sessions.length > 0 && (
        <div style={{ marginBottom: 4, paddingLeft: 4 }}>
          <Checkbox
            checked={allSelected}
            indeterminate={!allSelected && selectedIds.size > 0}
            onChange={handleToggleAll}
          >
            全选
          </Checkbox>
        </div>
      )}

      {/* 会话列表 */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <List
          size="small"
          dataSource={sessions}
          locale={{ emptyText: '暂无会话' }}
          renderItem={(item) => {
            const active = item.sessionId === sessionId;
            const checked = selectedIds.has(item.sessionId);
            return (
              <List.Item
                style={{
                  borderRadius: 6,
                  padding: '4px 6px',
                  borderLeft: active ? '3px solid #1976d2' : '3px solid transparent',
                  background: active ? '#e3f2fd' : checked ? '#f0f0f0' : 'transparent',
                  transition: 'background 0.2s ease',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
                  <Checkbox
                    checked={checked}
                    onChange={() => toggleSelect(item.sessionId)}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <div
                    style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
                    onClick={() => { void switchSession(item.sessionId); }}
                  >
                    <div
                      style={{
                        fontWeight: 500,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {item.title || '未命名'}
                    </div>
                    <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>
                      {item.updatedAt}
                    </div>
                  </div>
                  <Popconfirm
                    title="确认删除该会话"
                    description="将一并删除所有对话内容 不可恢复"
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    cancelText="取消"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      void handleDelete(item.sessionId);
                    }}
                    onCancel={(e) => e?.stopPropagation()}
                  >
                    <Button
                      danger
                      size="small"
                      type="text"
                      icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                      aria-label="删除会话"
                    />
                  </Popconfirm>
                </div>
              </List.Item>
            );
          }}
        />
      </div>
    </div>
  );
}

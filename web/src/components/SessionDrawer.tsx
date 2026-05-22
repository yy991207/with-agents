// 侧边抽屉:显示会话列表
// 每行右侧带 antd Popconfirm 二次确认的删除按钮
import { Button, List, Popconfirm, message } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { deleteSession } from '../api/http';
import { useSession } from '../hooks/useSession';
import { useChat } from '../state/ChatContext';

export interface SessionDrawerProps {
  // 预留:外层可控制开/关,这里占位渲染列表
  onClose?: () => void;
}

// 提取错误信息文本兜底
function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function SessionDrawer({ onClose }: SessionDrawerProps) {
  const { sessions, sessionId, switchSession } = useSession();
  const { dispatch } = useChat();

  // 删除会话:服务端返回 409 表示该会话还有进行中的 task,需要给提示而不是直接抛错
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
        // 后端已经没有该会话,前端做幂等清理即可
        dispatch({ type: 'session.deleted', sessionId: sid });
        message.info('该会话已不存在 已从列表中移除');
      } else {
        message.error(`删除失败 ${msg}`);
      }
    }
  };

  return (
    <div style={{ padding: 12, height: '100%', overflowY: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <strong>会话</strong>
        <Button size="small" onClick={() => switchSession(null)}>
          新会话
        </Button>
      </div>
      <List
        size="small"
        dataSource={sessions}
        locale={{ emptyText: '暂无会话' }}
        renderItem={(item) => {
          const active = item.sessionId === sessionId;
          return (
            <List.Item
              style={{
                cursor: 'pointer',
                background: active ? '#e3f2fd' : 'transparent',
                borderRadius: 6,
                padding: '6px 8px',
                borderLeft: active ? '3px solid #1976d2' : '3px solid transparent',
                transition: 'background 0.2s ease',
              }}
              onClick={() => {
                switchSession(item.sessionId);
                onClose?.();
              }}
            >
              {/* flex 主内容 + 删除按钮的两栏布局,避免覆盖原有点击区域 */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
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
                    // Popconfirm 在确认时也要阻止冒泡,免得切到这条 session
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
  );
}

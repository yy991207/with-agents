// 侧边抽屉:显示会话列表
import { Button, List } from 'antd';
import { useSession } from '../hooks/useSession';

export interface SessionDrawerProps {
  // 预留:外层可控制开/关,这里占位渲染列表
  onClose?: () => void;
}

export default function SessionDrawer({ onClose }: SessionDrawerProps) {
  const { sessions, sessionId, switchSession } = useSession();

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
        renderItem={(item) => (
          <List.Item
            style={{
              cursor: 'pointer',
              background: item.sessionId === sessionId ? '#e3f2fd' : 'transparent',
              borderRadius: 6,
              padding: '6px 8px',
            }}
            onClick={() => {
              switchSession(item.sessionId);
              onClose?.();
            }}
          >
            <div style={{ width: '100%' }}>
              <div style={{ fontWeight: 500 }}>{item.title || '未命名'}</div>
              <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>{item.updatedAt}</div>
            </div>
          </List.Item>
        )}
      />
    </div>
  );
}

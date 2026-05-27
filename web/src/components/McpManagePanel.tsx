// MCP 可视化管理面板: 以表格形式管理 MCP 服务器 参考 SkillsPanel 的表格编辑模式
// 后端 API: GET/POST /api/mcp/servers, PUT/DELETE /api/mcp/servers/{name}, PUT /api/mcp/servers/{name}/toggle
// 与 McpSettingsPanel（JSON 编辑器）共享同一份 mcp_config 数据 通过 Tabs 切换
import { useEffect, useState, useCallback } from 'react';
import {
  Button,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import type { McpServerItem } from '../api/http';
import {
  listMcpServers,
  createMcpServer,
  updateMcpServer,
  deleteMcpServer,
  toggleMcpServer,
  reloadMcpAgents,
} from '../api/http';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

// 本地编辑稿
interface McpServerDraft extends McpServerItem {
  dirty: boolean;
  isNew: boolean;
}

// 新建/编辑时的表单状态
interface McpFormState {
  name: string;
  transport: string;
  command: string;
  argsText: string;
  envText: string;
  url: string;
  headersText: string;
  alwaysAllowText: string;
  disabled: boolean;
}

export default function McpManagePanel() {
  const [servers, setServers] = useState<McpServerDraft[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reloading, setReloading] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<McpFormState | null>(null);
  const [editOriginal, setEditOriginal] = useState<McpServerDraft | null>(null);

  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listMcpServers();
      const list = resp.servers ?? [];
      setServers(list.map((s) => ({
        ...s,
        dirty: false,
        isNew: false,
      })));
    } catch (e) {
      message.error(`加载 MCP 服务器列表失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadServers(); }, []);

  // 将 McpServerDraft 转成编辑表单
  const draftToForm = (d: McpServerDraft): McpFormState => ({
    name: d.name,
    transport: d.transport || 'stdio',
    command: d.command || '',
    argsText: (d.args || []).join('\n'),
    envText: Object.entries(d.env || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    url: d.url || '',
    headersText: Object.entries(d.headers || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
    alwaysAllowText: (d.always_allow || []).join('\n'),
    disabled: d.disabled,
  });

  // 将表单转成 McpServerItem
  const formToItem = (f: McpFormState): McpServerItem => {
    const args = f.argsText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0);
    const env: Record<string, string> = {};
    f.envText.split('\n').forEach((line) => {
      const idx = line.indexOf('=');
      if (idx > 0) {
        env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
      }
    });
    const headers: Record<string, string> = {};
    f.headersText.split('\n').forEach((line) => {
      const idx = line.indexOf('=');
      if (idx > 0) {
        headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
      }
    });
    const always_allow = f.alwaysAllowText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0);

    return {
      name: f.name.trim(),
      transport: f.transport,
      command: f.command.trim() || null,
      args,
      env,
      url: f.url.trim() || null,
      headers,
      always_allow,
      disabled: f.disabled,
      updated_at: '',
      last_load_status: '',
      last_load_error: '',
      last_loaded_at: '',
    };
  };

  const handleAdd = () => {
    setEditForm({
      name: '',
      transport: 'stdio',
      command: '',
      argsText: '',
      envText: '',
      url: '',
      headersText: '',
      alwaysAllowText: '',
      disabled: false,
    });
    setEditOriginal(null);
    setEditOpen(true);
  };

  const handleEdit = (record: McpServerDraft) => {
    setEditForm(draftToForm(record));
    setEditOriginal(record);
    setEditOpen(true);
  };

  const handleFieldChange = (field: keyof McpFormState, value: string | boolean) => {
    if (!editForm) return;
    setEditForm({ ...editForm, [field]: value });
  };

  const handleEditOk = async () => {
    if (!editForm) return;
    const trimmed = editForm.name.trim();
    if (!trimmed) { message.warning('服务器名称不能为空'); return; }

    const isStdio = editForm.transport === 'stdio';
    if (isStdio && !editForm.command.trim()) {
      message.warning('stdio 模式下 command 不能为空');
      return;
    }
    if (!isStdio && !editForm.url.trim()) {
      message.warning('sse/streamable_http 模式下 url 不能为空');
      return;
    }

    const duplicate = servers.find((s) => s.name === trimmed && s !== editOriginal);
    if (duplicate) { message.warning(`已存在同名服务器: ${trimmed}`); return; }

    setSaving(true);
    try {
      const item = formToItem(editForm);
      if (editOriginal && !editOriginal.isNew) {
        await updateMcpServer(editOriginal.name, {
          transport: item.transport,
          command: item.command,
          args: item.args,
          env: item.env,
          url: item.url,
          headers: item.headers,
          always_allow: item.always_allow,
          disabled: item.disabled,
        });
        message.success(`服务器 "${trimmed}" 已更新`);
      } else {
        await createMcpServer(item);
        message.success(`服务器 "${trimmed}" 已创建`);
      }
      setEditOpen(false);
      setEditForm(null);
      setEditOriginal(null);
      await loadServers();
      message.info('配置已保存，点击右上角重载应用后即可即时生效');
    } catch (e) {
      message.error(`保存失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (record: McpServerDraft) => {
    Modal.confirm({
      title: `确认删除 MCP 服务器 ${record.name}`,
      content: '删除后该服务器的工具不再注入到 agent',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        try {
          await deleteMcpServer(record.name);
          message.success(`服务器 "${record.name}" 已删除`);
          await loadServers();
          message.info('删除已保存，点击右上角重载应用后即可即时生效');
        } catch (e) {
          message.error(`删除失败:${e instanceof Error ? e.message : String(e)}`);
        }
      },
    });
  };

  const handleToggle = async (record: McpServerDraft, checked: boolean) => {
    try {
      await toggleMcpServer(record.name, { disabled: !checked });
      setServers((prev) => prev.map((s) =>
        s.name === record.name ? { ...s, disabled: !checked } : s
      ));
      message.success(`服务器 "${record.name}" 已${checked ? '启用' : '禁用'}`);
      message.info('开关已保存，点击右上角重载应用后即可即时生效');
    } catch (e) {
      message.error(`操作失败:${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleReload = async () => {
    setReloading(true);
    try {
      const resp = await reloadMcpAgents();
      await loadServers();
      message.success(`已重载 ${resp.reloaded} 个 agent，最新 MCP 配置已即时生效`);
    } catch (e) {
      message.error(`重载失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setReloading(false);
    }
  };

  const renderLoadStatus = (record: McpServerDraft) => {
    if (record.disabled) {
      return <Text type="secondary">已禁用</Text>;
    }
    if (record.last_load_status === 'loaded') {
      return (
        <Space direction="vertical" size={2}>
          <Tag color="success" style={{ marginInlineEnd: 0 }}>已加载</Tag>
          {record.last_loaded_at ? (
            <Text type="secondary" style={{ fontSize: 12 }}>{record.last_loaded_at}</Text>
          ) : null}
        </Space>
      );
    }
    if (record.last_load_status === 'failed') {
      return (
        <Space direction="vertical" size={2}>
          <Tooltip title={record.last_load_error || '加载失败'}>
            <Tag color="error" style={{ marginInlineEnd: 0, cursor: 'help' }}>加载失败</Tag>
          </Tooltip>
          {record.last_load_error ? (
            <Tooltip title={record.last_load_error}>
              <Text type="danger" style={{ fontSize: 12, maxWidth: 240 }} ellipsis>
                {record.last_load_error}
              </Text>
            </Tooltip>
          ) : null}
        </Space>
      );
    }
    return <Text type="secondary">未重载</Text>;
  };

  const columns = [
    {
      title: '名称', dataIndex: 'name' as const, key: 'name', width: 200,
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: '状态', dataIndex: 'disabled' as const, key: 'disabled', width: 80,
      render: (disabled: boolean, record: McpServerDraft) => (
        <Switch size="small" checked={!disabled} onChange={(v) => handleToggle(record, v)} />
      ),
    },
    {
      title: '加载状态', key: 'load_status', width: 260,
      render: (_: unknown, record: McpServerDraft) => renderLoadStatus(record),
    },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_: unknown, record: McpServerDraft) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Title level={5} style={{ margin: 0 }}>MCP 服务器管理</Title>
        <Space>
          <Tooltip title="重载应用">
            <Button
              aria-label="重载应用"
              type="text"
              shape="circle"
              icon={<ReloadOutlined />}
              onClick={handleReload}
              loading={reloading}
              disabled={saving || loading}
            />
          </Tooltip>
          <Tooltip title="新增服务器">
            <Button
              aria-label="新增服务器"
              type="text"
              shape="circle"
              icon={<PlusOutlined />}
              onClick={handleAdd}
              disabled={saving || loading}
            />
          </Tooltip>
        </Space>
      </div>
      <Paragraph type="secondary" style={{ marginTop: 4 }}>
        可视化管理 MCP 服务器。每个服务器提供一组工具供 agent 调用。
        编辑完配置后点击“重载应用”，不用重启整个服务，最新 MCP 修改就会即时生效。
      </Paragraph>
      <div style={{ marginBottom: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          重载后会刷新每个 MCP 的最近加载结果。某个 server 启动失败时，会在表格里直接显示异常提示。
        </Text>
      </div>

      <Table<McpServerDraft>
        columns={columns}
        dataSource={servers}
        rowKey="name"
        loading={loading}
        size="small"
        pagination={false}
        scroll={{ x: 'max-content' }}
        // width: fit-content 让表格按列宽合计撑开  不再被父容器拉满  避免右侧大块空白
        style={{ marginBottom: 12, width: 'fit-content', maxWidth: '100%' }}
        locale={{ emptyText: '暂无 MCP 服务器，点击"新增服务器"添加' }}
      />

      <Modal
        title={editOriginal && !editOriginal.isNew ? `编辑服务器: ${editOriginal.name}` : '新增 MCP 服务器'}
        open={editOpen}
        onCancel={() => { setEditOpen(false); setEditForm(null); setEditOriginal(null); }}
        onOk={handleEditOk}
        okText="确定"
        cancelText="取消"
        confirmLoading={saving}
        destroyOnClose
        width={640}
      >
        {editForm && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>名称 (唯一标识)</Text>
              <Input
                value={editForm.name}
                placeholder="如 playwright / tencentcloud-sdk-mcp"
                onChange={(e) => handleFieldChange('name', e.target.value)}
                maxLength={64}
                disabled={editOriginal !== null && !editOriginal.isNew}
              />
            </div>

            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>传输方式</Text>
              <Select
                value={editForm.transport}
                style={{ width: '100%' }}
                onChange={(v) => handleFieldChange('transport', v)}
                options={[
                  { label: 'stdio (本地进程)', value: 'stdio' },
                  { label: 'sse (Server-Sent Events)', value: 'sse' },
                  { label: 'streamable_http', value: 'streamable_http' },
                ]}
              />
            </div>

            {editForm.transport === 'stdio' && (
              <>
                <div>
                  <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>Command (可执行文件路径 如 npx / uvx / python)</Text>
                  <Input
                    value={editForm.command}
                    placeholder="npx"
                    onChange={(e) => handleFieldChange('command', e.target.value)}
                  />
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>Args (每行一个参数)</Text>
                  <TextArea
                    value={editForm.argsText}
                    rows={3}
                    placeholder={"-y\n@playwright/mcp@latest\n--extension"}
                    style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}
                    onChange={(e) => handleFieldChange('argsText', e.target.value)}
                  />
                </div>
              </>
            )}

            {editForm.transport !== 'stdio' && (
              <div>
                <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>URL (服务器地址)</Text>
                <Input
                  value={editForm.url}
                  placeholder="https://mcp-server.example.com/sse"
                  onChange={(e) => handleFieldChange('url', e.target.value)}
                />
              </div>
            )}

            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>环境变量 (每行一个 KEY=VALUE)</Text>
              <TextArea
                value={editForm.envText}
                rows={3}
                placeholder={"PLAYWRIGHT_MCP_EXTENSION_TOKEN=xxx\nNODE_ENV=production"}
                style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}
                onChange={(e) => handleFieldChange('envText', e.target.value)}
              />
            </div>

            {editForm.transport !== 'stdio' && (
              <div>
                <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>Headers (每行一个 KEY=VALUE)</Text>
                <TextArea
                  value={editForm.headersText}
                  rows={2}
                  placeholder={"Authorization=Bearer xxx\nX-Custom-Header=value"}
                  style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}
                  onChange={(e) => handleFieldChange('headersText', e.target.value)}
                />
              </div>
            )}

            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>Always Allow (自动批准的工具名 每行一个)</Text>
              <TextArea
                value={editForm.alwaysAllowText}
                rows={2}
                placeholder={"browser_navigate\nbrowser_snapshot\nbrowser_click"}
                style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 12 }}
                onChange={(e) => handleFieldChange('alwaysAllowText', e.target.value)}
              />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Switch checked={!editForm.disabled} onChange={(v) => handleFieldChange('disabled', !v)} />
              <Text>启用（关闭后该服务器不参与工具加载）</Text>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

// 配置抽屉:数字员工管理
// 设计要点:
// 1. 顶部说明区
// 2. agent Tabs(editable-card 模式) 可加可删 至少保留 1 个
// 3. 每个 tab 内一套完整 form 包括 displayName / baseUrl / apiKey / model / availableModels / prompt
// 4. 底部 judge 选择 一行 Radio.Group
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  message,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import {
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { useSettings } from '../hooks/useSettings';
import { getAgentColor } from '../theme/tokens';
import type {
  AgentEditDraft,
  CreateAgentRequest,
  ModelView,
} from '../state/types';

const { Paragraph, Text } = Typography;

// 仅前端使用的可用模型行 带稳定 key 用于 Form.List 风格渲染
interface ModelDraftRow extends ModelView {
  _key: string;
}

function makeRow(model_id = '', label = ''): ModelDraftRow {
  const k = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  return { _key: k, model_id, label };
}

// ====== 新建 agent Modal ======
interface CreateAgentModalProps {
  open: boolean;
  onCancel: () => void;
  onSubmit: (body: CreateAgentRequest) => Promise<boolean>;
}

function CreateAgentModal({ open, onCancel, onSubmit }: CreateAgentModalProps) {
  const [displayName, setDisplayName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [models, setModels] = useState<ModelDraftRow[]>([makeRow()]);
  const [submitting, setSubmitting] = useState(false);

  // open 切换时重置表单
  useEffect(() => {
    if (!open) return;
    setDisplayName('');
    setBaseUrl('');
    setApiKey('');
    setModel('');
    setPrompt('');
    setModels([makeRow()]);
    setSubmitting(false);
  }, [open]);

  const addRow = () => setModels((prev) => [...prev, makeRow()]);
  const removeRow = (key: string) =>
    setModels((prev) => prev.filter((r) => r._key !== key));
  const updateRow = (key: string, field: 'model_id' | 'label', value: string) =>
    setModels((prev) =>
      prev.map((r) => (r._key === key ? { ...r, [field]: value } : r)),
    );

  const validate = (): string | null => {
    const n = displayName.trim();
    if (n.length < 1 || n.length > 64) return '显示名长度需为 1-64 字符';
    if (baseUrl.trim().length < 8) return 'Base URL 至少 8 个字符';
    if (apiKey.trim().length < 4) return 'API Key 至少 4 个字符';
    if (model.trim().length < 1) return '当前模型不能为空';
    if (prompt.trim().length < 5) return 'System Prompt 至少 5 个字';
    for (const r of models) {
      if (r.model_id.trim() || r.label.trim()) {
        if (!r.model_id.trim()) return '可用模型行的 model_id 不能为空';
      }
    }
    return null;
  };

  const handleOk = async () => {
    const err = validate();
    if (err) {
      message.warning(err);
      return;
    }
    setSubmitting(true);
    const cleanedModels: ModelView[] = models
      .filter((r) => r.model_id.trim())
      .map((r) => ({
        model_id: r.model_id.trim(),
        label: r.label.trim() || r.model_id.trim(),
      }));
    const body: CreateAgentRequest = {
      display_name: displayName.trim(),
      base_url: baseUrl.trim(),
      api_key: apiKey,
      model: model.trim(),
      prompt,
      available_models: cleanedModels,
    };
    const ok = await onSubmit(body);
    setSubmitting(false);
    if (ok) onCancel();
  };

  return (
    <Modal
      title="新增数字员工"
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      okText="创建"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
      width={560}
    >
      <Form layout="vertical">
        <Form.Item label="显示名" required>
          <Input
            value={displayName}
            placeholder="如 客服小花 / 编程助手"
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={64}
            allowClear
          />
        </Form.Item>
        <Form.Item label="API 提供商">
          <Select
            value="openai_compatible"
            disabled
            options={[{ label: 'OpenAI Compatible', value: 'openai_compatible' }]}
          />
        </Form.Item>
        <Form.Item label="Base URL" required>
          <Input
            value={baseUrl}
            placeholder="https://api.openai.com/v1"
            onChange={(e) => setBaseUrl(e.target.value)}
            allowClear
          />
        </Form.Item>
        <Form.Item label="API Key" required>
          <Input.Password
            value={apiKey}
            placeholder="sk-xxxxxxxx"
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="new-password"
          />
        </Form.Item>
        <Form.Item label="当前模型" required>
          <Input
            value={model}
            placeholder="如 gpt-4o"
            onChange={(e) => setModel(e.target.value)}
            allowClear
          />
        </Form.Item>
        <Form.Item label="System Prompt" required>
          <Input.TextArea
            value={prompt}
            rows={5}
            placeholder="该 agent 的系统提示词 至少 5 个字"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </Form.Item>
        <Form.Item label="可用模型(可选)" style={{ marginBottom: 0 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            {models.map((row) => (
              <Space.Compact key={row._key} style={{ width: '100%' }}>
                <Input
                  style={{ width: '40%' }}
                  placeholder="model_id 如 gpt-4o"
                  value={row.model_id}
                  onChange={(e) => updateRow(row._key, 'model_id', e.target.value)}
                />
                <Input
                  style={{ width: '50%' }}
                  placeholder="label 如 GPT-4o"
                  value={row.label}
                  onChange={(e) => updateRow(row._key, 'label', e.target.value)}
                />
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => removeRow(row._key)}
                  disabled={models.length <= 1}
                />
              </Space.Compact>
            ))}
            <Button type="dashed" icon={<PlusOutlined />} onClick={addRow} block>
              添加模型
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ====== 单个 agent 的表单 ======
interface AgentFormProps {
  draft: AgentEditDraft;
  saving: boolean;
  onPatch: (patch: Partial<AgentEditDraft>) => void;
  onSave: () => void;
  onReset: () => void;
}

function AgentForm({
  draft,
  saving,
  onPatch,
  onSave,
  onReset,
}: AgentFormProps) {
  // 当前 model 是否在 availableModels 内 否则默认进自定义
  const modelIsKnown = useMemo(
    () => draft.availableModels.some((m) => m.model_id === draft.model),
    [draft.availableModels, draft.model],
  );
  const [customMode, setCustomMode] = useState<boolean>(
    !modelIsKnown && Boolean(draft.model),
  );

  // 可用模型本地行视图 用稳定 key 避免抖动
  const [rows, setRows] = useState<ModelDraftRow[]>(() =>
    draft.availableModels.length > 0
      ? draft.availableModels.map((m) => makeRow(m.model_id, m.label))
      : [makeRow()],
  );

  // draft 切换 agent 或外部 reset 时同步 rows
  useEffect(() => {
    setRows(
      draft.availableModels.length > 0
        ? draft.availableModels.map((m) => makeRow(m.model_id, m.label))
        : [makeRow()],
    );
    // 同步 customMode 仅在 draft.model 变更时矫正
    setCustomMode(
      !draft.availableModels.some((m) => m.model_id === draft.model) &&
        Boolean(draft.model),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.name, draft.version]);

  // 把 rows 变更同步到 draft
  const flushRowsToDraft = (next: ModelDraftRow[]) => {
    setRows(next);
    const cleaned: ModelView[] = next
      .filter((r) => r.model_id.trim())
      .map((r) => ({
        model_id: r.model_id.trim(),
        label: r.label.trim() || r.model_id.trim(),
      }));
    onPatch({ availableModels: cleaned });
  };

  const addRow = () => flushRowsToDraft([...rows, makeRow()]);
  const removeRow = (key: string) =>
    flushRowsToDraft(rows.filter((r) => r._key !== key));
  const updateRowField = (
    key: string,
    field: 'model_id' | 'label',
    value: string,
  ) =>
    flushRowsToDraft(
      rows.map((r) => (r._key === key ? { ...r, [field]: value } : r)),
    );

  const modelOptions = useMemo(
    () =>
      draft.availableModels.map((m) => ({
        label: `${m.label}（${m.model_id}）`,
        value: m.model_id,
      })),
    [draft.availableModels],
  );

  const headerColor = getAgentColor(draft.name);

  return (
    <Form layout="vertical" disabled={saving}>
      <Space style={{ marginBottom: 12 }} size="small" wrap>
        <Tag color="blue">v{draft.version}</Tag>
        <Tag style={{ background: headerColor, color: '#fff', borderColor: headerColor }}>
          {draft.displayName || draft.name}
        </Tag>
        {draft.dirty ? <Tag color="orange">未保存</Tag> : <Tag>已同步</Tag>}
        <Text type="secondary" style={{ fontSize: 12 }}>
          内部 ID:{draft.name}
        </Text>
      </Space>

      <Form.Item label="显示名" required>
        <Input
          value={draft.displayName}
          maxLength={64}
          placeholder="1-64 字符"
          onChange={(e) => onPatch({ displayName: e.target.value })}
        />
      </Form.Item>

      <Form.Item label="API 提供商">
        <Select
          value={draft.providerType || 'openai_compatible'}
          disabled
          options={[{ label: 'OpenAI Compatible', value: 'openai_compatible' }]}
        />
      </Form.Item>

      <Form.Item label="Base URL" required>
        <Input
          value={draft.baseUrl}
          placeholder="https://api.openai.com/v1"
          onChange={(e) => onPatch({ baseUrl: e.target.value })}
          allowClear
        />
      </Form.Item>

      <Form.Item
        label="API Key"
        help={
          draft.apiKeyDirty
            ? '将在保存时覆盖原 Key'
            : `留空保留原 Key 当前:${draft.apiKeyMask || '(无)'}`
        }
      >
        <Input.Password
          value={draft.apiKey}
          placeholder={`留空保留 当前:${draft.apiKeyMask || '(无)'}`}
          onChange={(e) => onPatch({ apiKey: e.target.value })}
          autoComplete="new-password"
        />
      </Form.Item>

      <Form.Item label="当前模型" required>
        {customMode ? (
          <Space.Compact style={{ width: '100%' }}>
            <Input
              style={{ width: '100%' }}
              value={draft.model}
              placeholder="自定义 model_id 如 deepseek-v4-pro"
              onChange={(e) => onPatch({ model: e.target.value })}
              allowClear
            />
            <Button
              onClick={() => {
                if (modelOptions.length > 0 && !modelIsKnown) {
                  onPatch({ model: String(modelOptions[0].value) });
                }
                setCustomMode(false);
              }}
              disabled={modelOptions.length === 0}
            >
              选择
            </Button>
          </Space.Compact>
        ) : (
          <Space.Compact style={{ width: '100%' }}>
            <Select
              style={{ width: '100%' }}
              value={draft.model || undefined}
              placeholder="选择模型"
              showSearch
              optionFilterProp="label"
              options={modelOptions}
              onChange={(v) => onPatch({ model: String(v) })}
              notFoundContent={
                modelOptions.length === 0
                  ? '当前 agent 暂无候选模型 请点自定义'
                  : undefined
              }
            />
            <Button onClick={() => setCustomMode(true)}>自定义</Button>
          </Space.Compact>
        )}
      </Form.Item>

      <Form.Item label="可用模型列表">
        <Space direction="vertical" style={{ width: '100%' }}>
          {rows.map((row) => (
            <Space.Compact key={row._key} style={{ width: '100%' }}>
              <Input
                style={{ width: '40%' }}
                placeholder="model_id"
                value={row.model_id}
                onChange={(e) =>
                  updateRowField(row._key, 'model_id', e.target.value)
                }
              />
              <Input
                style={{ width: '50%' }}
                placeholder="label"
                value={row.label}
                onChange={(e) =>
                  updateRowField(row._key, 'label', e.target.value)
                }
              />
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={() => removeRow(row._key)}
                disabled={rows.length <= 1}
              />
            </Space.Compact>
          ))}
          <Button type="dashed" icon={<PlusOutlined />} onClick={addRow} block>
            添加模型
          </Button>
        </Space>
      </Form.Item>

      <Form.Item label="System Prompt" required>
        <Input.TextArea
          value={draft.prompt}
          rows={8}
          placeholder="该 agent 的系统提示词 至少 5 个字"
          onChange={(e) => onPatch({ prompt: e.target.value })}
        />
      </Form.Item>

      <Space>
        <Button type="primary" onClick={onSave} loading={saving} disabled={!draft.dirty}>
          保存
        </Button>
        <Button onClick={onReset} disabled={!draft.dirty}>
          重置
        </Button>
      </Space>
    </Form>
  );
}

// ====== 抽屉主体 ======
export default function SettingsDrawer() {
  const {
    state,
    closeDrawer,
    switchTab,
    setDraftField,
    save,
    reset,
    setJudge,
    createAgent,
    removeAgent,
  } = useSettings();
  const { open, loading, saving, drafts, judgeTarget, activeAgentName } = state;

  // 新增 Modal 开关
  const [createOpen, setCreateOpen] = useState(false);

  const items = useMemo(() => {
    const list = Object.values(drafts);
    return list.map((draft) => ({
      key: draft.name,
      label: (
        <span style={{ color: getAgentColor(draft.name), fontWeight: 600 }}>
          {draft.displayName || draft.name}
        </span>
      ),
      // 至少保留 1 个 不让全删光
      closable: list.length > 1,
      children: (
        <AgentForm
          draft={draft}
          saving={saving}
          onPatch={(patch) => setDraftField(draft.name, patch)}
          onSave={() => save(draft.name)}
          onReset={() => reset(draft.name)}
        />
      ),
    }));
  }, [drafts, saving, setDraftField, save, reset]);

  // editable-card 删除 tab 时走 Popconfirm 二次确认
  // antd Tabs onEdit 不直接支持 Popconfirm 这里只在用户按 X 时弹 Modal.confirm
  const handleTabEdit: React.ComponentProps<typeof Tabs>['onEdit'] = (
    targetKey,
    action,
  ) => {
    if (action === 'add') {
      setCreateOpen(true);
      return;
    }
    if (action === 'remove' && typeof targetKey === 'string') {
      const draft = drafts[targetKey];
      const label = draft?.displayName || targetKey;
      Modal.confirm({
        title: `确认删除数字员工 ${label}`,
        content: '该操作不可恢复 请确保没有进行中的对话依赖此 agent',
        okText: '删除',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: async () => {
          await removeAgent(targetKey);
        },
      });
    }
  };

  const handleCreate = async (body: CreateAgentRequest): Promise<boolean> => {
    return await createAgent(body);
  };

  return (
    <Drawer
      title="数字员工管理"
      placement="right"
      width={760}
      open={open}
      onClose={closeDrawer}
      destroyOnClose={false}
    >
      <Alert
        type="info"
        showIcon
        message="管理你的数字员工 自由命名 各自独立配置"
        description="每个 tab 是一个独立 agent 拥有自己的提供商/Key/模型/Prompt 数量可加可减 至少保留 1 个"
        style={{ marginBottom: 16 }}
      />

      <Spin spinning={loading} tip="加载配置中…">
        {items.length > 0 ? (
          <Tabs
            type="editable-card"
            activeKey={activeAgentName ?? undefined}
            onChange={(key) => switchTab(key)}
            onEdit={handleTabEdit}
            items={items}
            addIcon={<PlusOutlined />}
          />
        ) : (
          !loading && (
            <div
              style={{
                padding: 24,
                textAlign: 'center',
                background: '#fafafa',
                borderRadius: 8,
              }}
            >
              <Paragraph type="secondary">暂无数字员工 点下方按钮新建一个</Paragraph>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setCreateOpen(true)}
              >
                新增数字员工
              </Button>
            </div>
          )
        )}

        <Divider style={{ margin: '12px 0 16px' }} />

        <div>
          <Text strong>Judge 选择</Text>
          <Paragraph type="secondary" style={{ marginTop: 4 }}>
            选中即生效 由该 agent 负责自动决策
          </Paragraph>
          {Object.values(drafts).length === 0 ? (
            <Text type="secondary">暂无候选</Text>
          ) : (
            <Radio.Group
              value={judgeTarget ?? undefined}
              onChange={(e) => setJudge(String(e.target.value))}
              disabled={saving || loading}
            >
              <Space wrap>
                {Object.values(drafts).map((d) => (
                  <Radio key={d.name} value={d.name}>
                    <span
                      style={{
                        color: getAgentColor(d.name),
                        fontWeight: 600,
                      }}
                    >
                      {d.displayName || d.name}
                    </span>
                  </Radio>
                ))}
              </Space>
            </Radio.Group>
          )}
        </div>
      </Spin>

      <CreateAgentModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
      />
    </Drawer>
  );
}

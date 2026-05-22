// 配置抽屉:在右侧滑出 让用户管理 provider profile 与 4 个 agent 的配置
// 设计要点:
// 1. 顶部 provider profile 区  下拉选当前 profile 配新建 / 编辑 / 删除按钮
// 2. 中间 4 个 agent Tabs  每个 agent 选 provider + 选 model + 改 prompt
// 3. 底部 judge 选择
import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Card,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
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
  EditOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import type { TabsProps } from 'antd';
import { useSettings } from '../hooks/useSettings';
import { agentColors } from '../theme/tokens';
import type {
  AgentEditDraft,
  AgentName,
  CreateProfileRequest,
  ModelView,
  ProfileView,
  UpdateProfileRequest,
} from '../state/types';

const { Paragraph, Text } = Typography;

// 4 个 agent 的固定顺序,保证 tab 顺序与配色一致
const AGENT_ORDER: AgentName[] = ['DeepSeek', 'GLM', 'Kimi', 'Qwen'];

// ====== Provider profile 编辑 Modal ======
// 复用同一个 Modal 实现 新建 / 编辑两种模式 通过 mode prop 区分
type ProfileModalMode = 'create' | 'edit';

interface ProfileModalProps {
  open: boolean;
  mode: ProfileModalMode;
  // 编辑模式下传入当前 profile  新建模式可不传
  initial?: ProfileView | null;
  onCancel: () => void;
  // 返回值用于关闭 Modal  调用方根据是否成功决定关弹窗
  onSubmit: (
    name: string,
    body: CreateProfileRequest | UpdateProfileRequest,
    mode: ProfileModalMode,
  ) => Promise<boolean>;
}

interface ModelDraftRow extends ModelView {
  // 仅前端使用 用于在 Form.List 风格里给每行一个稳定 key
  _key: string;
}

function makeRow(model_id = '', label = ''): ModelDraftRow {
  // 简单生成行 key  毫秒 + 随机 4 位 足够 UI 内部去重
  const k = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  return { _key: k, model_id, label };
}

function ProfileModal({ open, mode, initial, onCancel, onSubmit }: ProfileModalProps) {
  // Modal 内本地表单态  关闭后会被重置
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  // 编辑模式下空字符串表示保留旧 api_key  新建模式必填
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState<ModelDraftRow[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // open 切换或 initial 变化时同步表单初值
  useEffect(() => {
    if (!open) return;
    if (mode === 'edit' && initial) {
      setName(initial.name);
      setBaseUrl(initial.base_url);
      // 编辑时不回显真实 key  让占位说明保留旧值
      setApiKey('');
      setModels(
        initial.models.length > 0
          ? initial.models.map((m) => makeRow(m.model_id, m.label))
          : [makeRow()],
      );
    } else {
      setName('');
      setBaseUrl('');
      setApiKey('');
      setModels([makeRow()]);
    }
    setSubmitting(false);
  }, [open, mode, initial]);

  // 模型行操作:新增 / 删除 / 编辑某字段
  const addRow = () => setModels((prev) => [...prev, makeRow()]);
  const removeRow = (key: string) =>
    setModels((prev) => prev.filter((r) => r._key !== key));
  const updateRow = (key: string, field: 'model_id' | 'label', value: string) =>
    setModels((prev) =>
      prev.map((r) => (r._key === key ? { ...r, [field]: value } : r)),
    );

  // 简单前端校验  对齐后端规则
  const validate = (): string | null => {
    if (mode === 'create') {
      const n = name.trim();
      if (n.length < 1 || n.length > 64) return '名称长度需为 1-64 字符';
    }
    if (baseUrl.trim().length < 8) return 'Base URL 至少 8 个字符';
    if (mode === 'create' && apiKey.trim().length < 4) {
      return 'API Key 至少 4 个字符';
    }
    // 模型行允许为空数组(后端默认 [])  但若填了行  model_id 必填
    for (const r of models) {
      if (r.model_id.trim() || r.label.trim()) {
        if (!r.model_id.trim()) return '模型 ID 不能为空';
      }
    }
    return null;
  };

  const handleOk = async () => {
    const err = validate();
    if (err) {
      // 不发请求 直接给提示
      message.warning(err);
      return;
    }
    setSubmitting(true);
    // 过滤掉完全空白的行
    const cleanedModels: ModelView[] = models
      .filter((r) => r.model_id.trim())
      .map((r) => ({ model_id: r.model_id.trim(), label: r.label.trim() || r.model_id.trim() }));

    let ok = false;
    if (mode === 'create') {
      const body: CreateProfileRequest = {
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey,
        models: cleanedModels,
      };
      ok = await onSubmit(body.name, body, 'create');
    } else {
      // 编辑模式:apiKey 为空表示保留旧值  非空才提交
      const body: UpdateProfileRequest = {
        base_url: baseUrl.trim(),
        models: cleanedModels,
      };
      if (apiKey.trim().length > 0) {
        body.api_key = apiKey;
      }
      ok = await onSubmit(initial?.name ?? '', body, 'edit');
    }
    setSubmitting(false);
    if (ok) onCancel();
  };

  return (
    <Modal
      title={mode === 'create' ? '新建 provider 配置' : `编辑 provider:${initial?.name ?? ''}`}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
      width={560}
    >
      <Form layout="vertical">
        <Form.Item label="名称" required>
          <Input
            value={name}
            placeholder="如 默认 / 备用 / 自建"
            onChange={(e) => setName(e.target.value)}
            disabled={mode === 'edit'}
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

        <Form.Item
          label="API Key"
          required={mode === 'create'}
          help={
            mode === 'edit'
              ? '留空表示保留原 key  填入新值则覆盖'
              : undefined
          }
        >
          <Input.Password
            value={apiKey}
            placeholder={
              mode === 'edit'
                ? `当前:${initial?.api_key ?? '(无)'}`
                : 'sk-xxxxxxxx'
            }
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="new-password"
          />
        </Form.Item>

        <Form.Item label="可用模型" style={{ marginBottom: 0 }}>
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
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              onClick={addRow}
              block
            >
              添加模型
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ====== Provider profile 顶部区域 ======
interface ProfileSectionProps {
  profiles: ProfileView[];
  loading: boolean;
  selected: string | null;
  onSelect: (name: string) => void;
  onCreate: (body: CreateProfileRequest) => Promise<boolean>;
  onUpdate: (name: string, body: UpdateProfileRequest) => Promise<boolean>;
  onDelete: (name: string) => Promise<boolean>;
}

function ProfileSection({
  profiles,
  loading,
  selected,
  onSelect,
  onCreate,
  onUpdate,
  onDelete,
}: ProfileSectionProps) {
  // 当前选中的 profile  没选中时取列表第一个
  const currentName = selected ?? profiles[0]?.name ?? null;
  const current = useMemo(
    () => profiles.find((p) => p.name === currentName) ?? null,
    [profiles, currentName],
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<ProfileModalMode>('create');

  const openCreate = () => {
    setModalMode('create');
    setModalOpen(true);
  };
  const openEdit = () => {
    if (!current) return;
    setModalMode('edit');
    setModalOpen(true);
  };

  const handleSubmit = async (
    name: string,
    body: CreateProfileRequest | UpdateProfileRequest,
    mode: ProfileModalMode,
  ): Promise<boolean> => {
    if (mode === 'create') {
      const ok = await onCreate(body as CreateProfileRequest);
      if (ok) onSelect(name);
      return ok;
    }
    return onUpdate(name, body as UpdateProfileRequest);
  };

  return (
    <Card
      title="提供商配置"
      size="small"
      style={{ marginBottom: 16 }}
      bodyStyle={{ paddingTop: 12 }}
    >
      <Space wrap style={{ marginBottom: 12, width: '100%' }}>
        <Text strong>配置文件</Text>
        <Select
          style={{ minWidth: 180 }}
          value={currentName ?? undefined}
          placeholder="请选择"
          loading={loading}
          onChange={onSelect}
          options={profiles.map((p) => ({ label: p.name, value: p.name }))}
        />
        <Button icon={<PlusOutlined />} onClick={openCreate}>
          新建
        </Button>
        <Button icon={<EditOutlined />} onClick={openEdit} disabled={!current}>
          编辑
        </Button>
        <Popconfirm
          title="确认删除该 provider"
          description="删除前请确保没有 agent 在使用该 provider"
          onConfirm={() => current && onDelete(current.name)}
          okText="删除"
          cancelText="取消"
          disabled={!current}
        >
          <Button danger icon={<DeleteOutlined />} disabled={!current}>
            删除
          </Button>
        </Popconfirm>
      </Space>

      {current ? (
        <div>
          <Form layout="horizontal" labelCol={{ span: 6 }} wrapperCol={{ span: 18 }} size="small">
            <Form.Item label="API 提供商" style={{ marginBottom: 6 }}>
              <Tag color="geekblue">{current.provider_type}</Tag>
            </Form.Item>
            <Form.Item label="Base URL" style={{ marginBottom: 6 }}>
              <Text code copyable>
                {current.base_url}
              </Text>
            </Form.Item>
            <Form.Item label="API Key" style={{ marginBottom: 6 }}>
              <Text code>{current.api_key || '(未设置)'}</Text>
            </Form.Item>
            <Form.Item label="可用模型" style={{ marginBottom: 0 }}>
              {current.models.length === 0 ? (
                <Text type="secondary">暂无</Text>
              ) : (
                <Space wrap>
                  {current.models.map((m) => (
                    <Tag key={m.model_id}>
                      {m.label}
                      <span style={{ color: '#999', marginLeft: 4 }}>({m.model_id})</span>
                    </Tag>
                  ))}
                </Space>
              )}
            </Form.Item>
          </Form>
        </div>
      ) : (
        !loading && <Empty description="暂无 provider 配置 点新建添加" />
      )}

      <ProfileModal
        open={modalOpen}
        mode={modalMode}
        initial={modalMode === 'edit' ? current : null}
        onCancel={() => setModalOpen(false)}
        onSubmit={handleSubmit}
      />
    </Card>
  );
}

// ====== 单个 agent 的表单 ======
interface AgentFormProps {
  draft: AgentEditDraft;
  saving: boolean;
  profiles: ProfileView[];
  onChange: (field: 'model' | 'prompt', value: string) => void;
  onProfileChange: (profileName: string) => void;
  onSave: () => void;
  onReset: () => void;
}

function AgentForm({
  draft,
  saving,
  profiles,
  onChange,
  onProfileChange,
  onSave,
  onReset,
}: AgentFormProps) {
  // 当前 profile 对应的可选模型
  const currentProfile = useMemo(
    () => profiles.find((p) => p.name === draft.profileName),
    [profiles, draft.profileName],
  );
  const modelOptions = useMemo(() => {
    const list = currentProfile?.models ?? [];
    return list.map((m) => ({
      label: `${m.label}（${m.model_id}）`,
      value: m.model_id,
    }));
  }, [currentProfile]);

  // 是否切到自定义模型 ID 输入  默认根据 draft.model 是否在 modelOptions 决定
  const modelIsKnown = useMemo(
    () => modelOptions.some((opt) => opt.value === draft.model),
    [modelOptions, draft.model],
  );
  const [customMode, setCustomMode] = useState<boolean>(!modelIsKnown && Boolean(draft.model));

  // 切换 profile 时如果当前 model 不在新 profile.models 内  自动选第一个
  // 注意:仅在用户主动切 profile 时触发 不要在初始化阶段强行覆盖
  const handleProfileChange = (next: string) => {
    onProfileChange(next);
    const np = profiles.find((p) => p.name === next);
    const list = np?.models ?? [];
    if (list.length === 0) {
      // 新 profile 没有候选模型  保留原 model 让用户自定义
      setCustomMode(true);
      return;
    }
    const stillValid = list.some((m) => m.model_id === draft.model);
    if (!stillValid) {
      // 自动选第一个候选
      onChange('model', list[0].model_id);
      setCustomMode(false);
    }
  };

  return (
    <Form layout="vertical" disabled={saving}>
      <Space style={{ marginBottom: 12 }} size="small" wrap>
        <Tag color="blue">v{draft.version}</Tag>
        {draft.dirty ? <Tag color="orange">未保存</Tag> : <Tag>已同步</Tag>}
      </Space>

      <Form.Item label="API 提供商" required>
        <Select
          value={draft.profileName || undefined}
          placeholder="选择 provider"
          options={profiles.map((p) => ({ label: p.name, value: p.name }))}
          onChange={handleProfileChange}
          notFoundContent={profiles.length === 0 ? '暂无 provider 请先在上方新建' : undefined}
        />
      </Form.Item>

      <Form.Item label="模型" required>
        {customMode ? (
          <Space.Compact style={{ width: '100%' }}>
            <Input
              style={{ width: '100%' }}
              value={draft.model}
              placeholder="自定义 model_id 如 deepseek-v4-pro"
              onChange={(e) => onChange('model', e.target.value)}
              allowClear
            />
            <Button
              onClick={() => {
                // 切回下拉  若候选非空且 draft.model 不在内 自动选第一个
                if (modelOptions.length > 0 && !modelIsKnown) {
                  onChange('model', String(modelOptions[0].value));
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
              onChange={(v) => onChange('model', String(v))}
              notFoundContent={
                modelOptions.length === 0
                  ? '当前 provider 暂无候选模型 请点自定义'
                  : undefined
              }
            />
            <Button onClick={() => setCustomMode(true)}>自定义</Button>
          </Space.Compact>
        )}
      </Form.Item>

      <Form.Item label="System Prompt" required>
        <Input.TextArea
          value={draft.prompt}
          rows={8}
          placeholder="该 agent 的系统提示词 至少 5 个字"
          onChange={(e) => onChange('prompt', e.target.value)}
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
    updateDraft,
    setAgentProfile,
    save,
    reset,
    setJudge,
    createNewProfile,
    saveProfile,
    removeProfile,
  } = useSettings();
  const { open, loading, saving, drafts, judgeTarget, profiles, profilesLoading } = state;

  // 当前在 ProfileSection 中选中的 profile  仅用于展示  与各 agent.draft.profileName 解耦
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null);

  // profiles 加载完默认选中第一个
  useEffect(() => {
    if (!selectedProfile && profiles.length > 0) {
      setSelectedProfile(profiles[0].name);
    }
    // 若选中的 profile 被删了  自动回退到第一个
    if (selectedProfile && !profiles.some((p) => p.name === selectedProfile)) {
      setSelectedProfile(profiles[0]?.name ?? null);
    }
  }, [profiles, selectedProfile]);

  // 把固定顺序的 agent 名映射成 Tabs items;若服务端少返了某个 agent 就跳过
  const items = useMemo<TabsProps['items']>(() => {
    return AGENT_ORDER.filter((name) => Boolean(drafts[name])).map((name) => {
      const draft = drafts[name] as AgentEditDraft;
      return {
        key: name,
        label: (
          <span style={{ color: agentColors[name], fontWeight: 600 }}>{name}</span>
        ),
        children: (
          <AgentForm
            draft={draft}
            saving={saving}
            profiles={profiles}
            onChange={(field, value) => updateDraft(name, field, value)}
            onProfileChange={(profileName) => setAgentProfile(name, profileName)}
            onSave={() => save(name)}
            onReset={() => reset(name)}
          />
        ),
      };
    });
  }, [drafts, saving, profiles, updateDraft, setAgentProfile, save, reset]);

  return (
    <Drawer
      title="配置管理"
      placement="right"
      width={760}
      open={open}
      onClose={closeDrawer}
      destroyOnClose={false}
    >
      <Paragraph type="secondary" style={{ marginTop: 0 }}>
        修改 provider/model/prompt 后保存立即生效 不需要重启服务
      </Paragraph>

      <Spin spinning={loading || profilesLoading} tip="加载配置中…">
        <ProfileSection
          profiles={profiles}
          loading={profilesLoading}
          selected={selectedProfile}
          onSelect={setSelectedProfile}
          onCreate={createNewProfile}
          onUpdate={saveProfile}
          onDelete={removeProfile}
        />

        {items && items.length > 0 ? (
          <Card title="Agent 配置" size="small" style={{ marginBottom: 16 }}>
            <Tabs items={items} />
          </Card>
        ) : (
          !loading && <Text type="secondary">暂无可编辑的 agent</Text>
        )}

        <Divider style={{ margin: '12px 0 16px' }} />

        <div>
          <Text strong>Judge 选择</Text>
          <Paragraph type="secondary" style={{ marginTop: 4 }}>
            选中即生效 由该 agent 负责自动决策
          </Paragraph>
          <Radio.Group
            value={judgeTarget ?? undefined}
            onChange={(e) => setJudge(String(e.target.value))}
            disabled={saving || loading}
          >
            {AGENT_ORDER.map((name) => (
              <Radio key={name} value={name}>
                <span style={{ color: agentColors[name], fontWeight: 600 }}>{name}</span>
              </Radio>
            ))}
          </Radio.Group>
        </div>
      </Spin>
    </Drawer>
  );
}

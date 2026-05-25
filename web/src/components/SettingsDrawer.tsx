// 配置抽屉:数字员工管理 + MCP 服务器配置
// 左侧菜单: 数字员工配置 | Judge 选择 | MCP 配置
// 右侧面板: 根据选中类目切换内容
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Avatar as AntAvatar,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Menu,
  message,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd';
import {
  ReloadOutlined,
  PlusOutlined,
  DeleteOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  discoverAgentModels,
  discoverModels,
} from '../api/http';
import { useSettings } from '../hooks/useSettings';
import { getAgentColor } from '../theme/tokens';
import type {
  AgentEditDraft,
  CreateAgentRequest,
  ModelView,
} from '../state/types';
import McpSettingsPanel from './McpSettingsPanel';
import SkillsPanel from './SkillsPanel';

const { Paragraph, Text } = Typography;

// ====== 新建 agent Modal ======
interface CreateAgentModalProps {
  open: boolean;
  onCancel: () => void;
  onSubmit: (body: CreateAgentRequest) => Promise<boolean>;
  // 从已有 agent 草稿中预填的字段，新建时复用 baseUrl / model / prompt 等
  initialBaseUrl?: string;
  initialApiKeyMask?: string;
  initialAgentName?: string;
  initialModel?: string;
  initialAvailableModels?: ModelView[];
  initialPrompt?: string;
}

function CreateAgentModal({
  open,
  onCancel,
  onSubmit,
  initialBaseUrl,
  initialApiKeyMask,
  initialAgentName,
  initialModel,
  initialAvailableModels,
  initialPrompt,
}: CreateAgentModalProps) {
  const [displayName, setDisplayName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [availableModels, setAvailableModels] = useState<ModelView[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // open 切换时重置表单，并从已有 agent 草稿中预填字段
  useEffect(() => {
    if (!open) return;
    setDisplayName('');
    setBaseUrl(initialBaseUrl ?? '');
    setApiKey(initialApiKeyMask ?? '');
    setModel(initialModel ?? '');
    setPrompt(initialPrompt ?? '');
    setAvailableModels(initialAvailableModels ?? []);
    setDiscovering(false);
    setSubmitting(false);
  }, [open, initialBaseUrl, initialApiKeyMask, initialModel, initialAvailableModels, initialPrompt]);

  const modelOptions = useMemo(
    () =>
      availableModels.map((m) => ({
        label: `${m.label}（${m.model_id}）`,
        value: m.model_id,
      })),
    [availableModels],
  );

  // 从已有 agent 复制 key 时不需要校验 apiKey
  const hasCopyKey = Boolean(initialAgentName);

  const validate = (): string | null => {
    const n = displayName.trim();
    if (n.length < 1 || n.length > 64) return '显示名长度需为 1-64 字符';
    if (baseUrl.trim().length < 8) return 'Base URL 至少 8 个字符';
    if (!hasCopyKey && apiKey.trim().length < 4) return 'API Key 至少 4 个字符';
    if (availableModels.length < 1) return '请先获取可用模型列表';
    if (model.trim().length < 1) return '当前模型不能为空';
    if (prompt.trim().length < 5) return 'System Prompt 至少 5 个字';
    return null;
  };

  const handleDiscover = async () => {
    if (baseUrl.trim().length < 8) {
      message.warning('Base URL 至少 8 个字符');
      return;
    }
    setDiscovering(true);
    try {
      let models: ModelView[];
      if (initialAgentName) {
        // 预填了 mask key，走后端 /agents/{name}/models/discover 用真实 key 拉模型
        const resp = await discoverAgentModels(initialAgentName, {
          base_url: baseUrl.trim(),
          provider_type: 'openai_compatible',
        });
        models = resp.models;
      } else {
        const resp = await discoverModels({
          base_url: baseUrl.trim(),
          api_key: apiKey,
          provider_type: 'openai_compatible',
        });
        models = resp.models;
      }
      // discover 接口拿到的模型列表  max_input_tokens 字段后端默认给 200000
      // 这里再做一遍兜底  防止后端响应缺字段时前端类型不一致
      models = models.map((m) => ({
        ...m,
        max_input_tokens:
          typeof m.max_input_tokens === 'number' && m.max_input_tokens > 0
            ? m.max_input_tokens
            : 200000,
      }));
      setAvailableModels(models);
      setModel((cur) =>
        models.some((m) => m.model_id === cur)
          ? cur
          : models[0]?.model_id ?? '',
      );
      message.success(`已获取 ${models.length} 个可用模型`);
    } catch (e) {
      message.error(`获取模型失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDiscovering(false);
    }
  };

  const handleOk = async () => {
    const err = validate();
    if (err) {
      message.warning(err);
      return;
    }
    setSubmitting(true);
    const body: CreateAgentRequest = {
      display_name: displayName.trim(),
      base_url: baseUrl.trim(),
      model: model.trim(),
      prompt,
      available_models: availableModels,
    };
    if (initialAgentName) {
      body.copy_key_from = initialAgentName;
    } else {
      body.api_key = apiKey;
    }
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
            onChange={(e) => {
              setBaseUrl(e.target.value);
              setAvailableModels([]);
              setModel('');
            }}
            allowClear
          />
        </Form.Item>
        <Form.Item label="API Key" required>
          <Input.Password
            value={apiKey}
            placeholder="sk-xxxxxxxx"
            onChange={(e) => {
              setApiKey(e.target.value);
              setAvailableModels([]);
              setModel('');
            }}
            autoComplete="new-password"
          />
        </Form.Item>
        <Form.Item label="当前模型" required>
          <Space.Compact style={{ width: '100%' }}>
            <Select
              style={{ width: '100%' }}
              value={model || undefined}
              placeholder="先获取模型列表 再选择"
              showSearch
              optionFilterProp="label"
              options={modelOptions}
              onChange={(v) => setModel(String(v))}
              disabled={modelOptions.length === 0}
              notFoundContent="暂无模型 请先获取"
            />
            <Button
              icon={<ReloadOutlined />}
              loading={discovering}
              onClick={handleDiscover}
            >
              获取模型
            </Button>
          </Space.Compact>
        </Form.Item>
        <Form.Item
          label="上下文窗口 (tokens)"
          required
          help="该模型的最大输入 token 数  会话总 token 超过此值 80% 时触发自动摘要压缩  默认 200000  实际值请参考模型供应商文档"
        >
          <InputNumber
            style={{ width: '100%' }}
            min={1}
            step={1000}
            placeholder="200000"
            value={
              availableModels.find((m) => m.model_id === model)
                ?.max_input_tokens ?? 200000
            }
            disabled={!model}
            onChange={(v) => {
              const next = typeof v === 'number' && v > 0 ? Math.floor(v) : 200000;
              setAvailableModels((cur) =>
                cur.map((m) =>
                  m.model_id === model ? { ...m, max_input_tokens: next } : m,
                ),
              );
            }}
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
  // 头像上传 / 移除 走独立路径  和 dirty / save 解耦
  onAvatarUpload: (file: File) => Promise<boolean>;
  onAvatarRemove: () => Promise<boolean>;
}

function AgentForm({
  draft,
  saving,
  onPatch,
  onSave,
  onReset,
  onAvatarUpload,
  onAvatarRemove,
}: AgentFormProps) {
  const [discovering, setDiscovering] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const modelOptions = useMemo(
    () =>
      draft.availableModels.map((m) => ({
        label: `${m.label}（${m.model_id}）`,
        value: m.model_id,
      })),
    [draft.availableModels],
  );

  const headerColor = getAgentColor(draft.name);

  const handleDiscover = async () => {
    if (draft.baseUrl.trim().length < 8) {
      message.warning('Base URL 至少 8 个字符');
      return;
    }
    if (draft.apiKeyDirty && draft.apiKey.length < 4) {
      message.warning('API Key 至少 4 个字符');
      return;
    }
    setDiscovering(true);
    try {
      const resp = await discoverAgentModels(draft.name, {
        base_url: draft.baseUrl.trim(),
        api_key: draft.apiKeyDirty ? draft.apiKey : undefined,
        provider_type: draft.providerType || 'openai_compatible',
      });
      // discover 拿到的模型列表  max_input_tokens 字段后端默认给 200000
      // 这里再做一遍兜底  防止后端响应缺字段时前端类型不一致
      const normalized = resp.models.map((m) => ({
        ...m,
        max_input_tokens:
          typeof m.max_input_tokens === 'number' && m.max_input_tokens > 0
            ? m.max_input_tokens
            : 200000,
      }));
      const nextModel = normalized.some((m) => m.model_id === draft.model)
        ? draft.model
        : normalized[0]?.model_id ?? '';
      onPatch({ availableModels: normalized, model: nextModel });
      message.success(`已获取 ${normalized.length} 个可用模型`);
    } catch (e) {
      message.error(`获取模型失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDiscovering(false);
    }
  };

  // 头像上传:本地先校验大小/格式 再走 onAvatarUpload
  // 后端有同样校验  这里只是优化体验避免无谓的 2MB 网络往返
  const _AVATAR_MAX = 2 * 1024 * 1024;
  const _AVATAR_TYPES = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif'];
  const handlePickFile = () => {
    fileInputRef.current?.click();
  };
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // 选完一次就 reset value 让选同一个文件也能再次触发 onChange
    e.target.value = '';
    if (!file) return;
    if (!_AVATAR_TYPES.includes(file.type)) {
      message.error('仅支持 PNG / JPEG / WebP / GIF 格式');
      return;
    }
    if (file.size > _AVATAR_MAX) {
      message.error('头像不能超过 2MB');
      return;
    }
    setAvatarBusy(true);
    try {
      await onAvatarUpload(file);
    } finally {
      setAvatarBusy(false);
    }
  };
  const handleRemoveAvatar = async () => {
    setAvatarBusy(true);
    try {
      await onAvatarRemove();
    } finally {
      setAvatarBusy(false);
    }
  };
  const avatarInitial = (draft.displayName || draft.name).slice(0, 1).toUpperCase();

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 18,
        boxShadow: '0 10px 24px rgba(15, 23, 42, 0.04)',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: 16 }}>
        {/* 头像区:独立于 Form  上传/移除直连后端  不参与 dirty/save */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />
        <div
          style={{
            alignItems: 'center',
            background: 'rgba(15, 23, 42, 0.02)',
            border: '1px solid #eef2f7',
            borderRadius: 12,
            display: 'flex',
            gap: 16,
            marginBottom: 16,
            padding: 12,
          }}
        >
          {draft.avatarDataUrl ? (
            <AntAvatar
              src={draft.avatarDataUrl}
              shape="circle"
              size={64}
              alt={draft.displayName || draft.name}
            />
          ) : (
            <AntAvatar
              shape="circle"
              size={64}
              style={{
                background: headerColor,
                color: '#fff',
                fontSize: 24,
                fontWeight: 600,
              }}
            >
              {avatarInitial}
            </AntAvatar>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: 'rgba(15, 23, 42, 0.92)', fontSize: 14, fontWeight: 600 }}>
              头像
            </div>
            <div style={{ color: 'rgba(71, 85, 105, 0.72)', fontSize: 12, marginTop: 2 }}>
              用于对话和会话列表展示  PNG / JPEG / WebP / GIF  ≤ 2MB
            </div>
          </div>
          <Space size={8}>
            <Button
              icon={<UploadOutlined />}
              size="small"
              loading={avatarBusy}
              onClick={handlePickFile}
            >
              {draft.avatarDataUrl ? '更换' : '上传'}
            </Button>
            {draft.avatarDataUrl ? (
              <Button
                size="small"
                danger
                disabled={avatarBusy}
                onClick={handleRemoveAvatar}
              >
                移除
              </Button>
            ) : null}
          </Space>
        </div>

        <Form layout="vertical" disabled={saving}>
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
              onChange={(e) =>
                onPatch({ baseUrl: e.target.value })
              }
              allowClear
            />
          </Form.Item>

          <Form.Item
            label="API Key"
            help={
              draft.apiKeyDirty
                ? '将在保存时覆盖原 Key'
                : draft.apiKeyMask
                  ? '输入新 Key 以覆盖当前密钥'
                  : '请设置 API Key'
            }
          >
            <Input.Password
              value={draft.apiKey}
              placeholder={draft.apiKeyMask ? '输入新 Key 以覆盖' : 'sk-xxxxxxxx'}
              onChange={(e) =>
                onPatch({ apiKey: e.target.value })
              }
              autoComplete="new-password"
            />
          </Form.Item>

          <Form.Item label="当前模型" required>
            <Space.Compact style={{ width: '100%' }}>
              <Select
                style={{ width: '100%' }}
                value={draft.model || undefined}
                placeholder="选择模型"
                showSearch
                optionFilterProp="label"
                options={modelOptions}
                onChange={(v) => onPatch({ model: String(v) })}
                disabled={modelOptions.length === 0}
                notFoundContent="暂无模型 请刷新列表"
              />
              <Button
                icon={<ReloadOutlined />}
                loading={discovering}
                onClick={handleDiscover}
              >
                刷新模型
              </Button>
            </Space.Compact>
          </Form.Item>

          <Form.Item
            label="上下文窗口 (tokens)"
            required
            help="该模型的最大输入 token 数  会话总 token 超过此值 80% 时触发自动摘要压缩  默认 200000  实际值请参考模型供应商文档"
          >
            <InputNumber
              style={{ width: '100%' }}
              min={1}
              step={1000}
              placeholder="200000"
              value={
                draft.availableModels.find((m) => m.model_id === draft.model)
                  ?.max_input_tokens ?? 200000
              }
              disabled={!draft.model}
              onChange={(v) => {
                const next =
                  typeof v === 'number' && v > 0 ? Math.floor(v) : 200000;
                onPatch({
                  availableModels: draft.availableModels.map((m) =>
                    m.model_id === draft.model
                      ? { ...m, max_input_tokens: next }
                      : m,
                  ),
                });
              }}
            />
          </Form.Item>

          <Form.Item label="System Prompt" required>
            <Input.TextArea
              value={draft.prompt}
              rows={8}
              placeholder="该 agent 的系统提示词 至少 5 个字"
              onChange={(e) => onPatch({ prompt: e.target.value })}
            />
          </Form.Item>

          <div
            style={{
              borderTop: '1px solid #eef2f7',
              display: 'flex',
              gap: 8,
              justifyContent: 'flex-end',
              marginTop: 20,
              paddingTop: 14,
            }}
          >
            <Button onClick={onReset} disabled={!draft.dirty}>
              重置
            </Button>
            <Button type="primary" onClick={onSave} loading={saving} disabled={!draft.dirty}>
              保存
            </Button>
          </div>
        </Form>
      </div>
    </div>
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
    uploadAvatar,
    removeAvatar,
  } = useSettings();
  const { open, loading, saving, drafts, judgeTarget, activeAgentName } = state;

  // 新增 Modal 开关
  const [createOpen, setCreateOpen] = useState(false);
  // 左侧类目：agents | judge
  const [settingsCategory, setSettingsCategory] = useState('agents');

  // 从当前选中 agent 的草稿中取预填值，新建弹窗打开时复用
  const activeDraft = activeAgentName ? drafts[activeAgentName] : null;
  const initialCreate = useMemo(() => {
    if (!activeDraft) return {};
    return {
      initialBaseUrl: activeDraft.baseUrl,
      initialApiKeyMask: activeDraft.apiKeyMask,
      initialAgentName: activeDraft.name,
      initialModel: activeDraft.model,
      initialAvailableModels: activeDraft.availableModels,
      initialPrompt: activeDraft.prompt,
    };
  }, [activeDraft?.baseUrl, activeDraft?.apiKeyMask, activeDraft?.name, activeDraft?.model, activeDraft?.availableModels, activeDraft?.prompt]);

  // 新增 agent 的回调
  const handleCreate = async (body: CreateAgentRequest): Promise<boolean> => {
    return await createAgent(body);
  };

  return (
    <Drawer
      title="设置"
      placement="right"
      width={820}
      open={open}
      onClose={closeDrawer}
      destroyOnClose={false}
      styles={{ header: { borderBottom: 'none' } }}
    >
      <Spin spinning={loading} tip="加载配置中…">
        <div
          style={{
            display: 'flex',
            gap: 16,
            height: '100%',
            minHeight: 640,
          }}
        >
          {/* 左侧类目菜单 */}
          <div
            style={{
              background: '#fff',
              border: '1px solid #e5e7eb',
              borderRadius: 16,
              flexShrink: 0,
              padding: 8,
              width: 168,
            }}
          >
            <Menu
              mode="inline"
              selectedKeys={[settingsCategory]}
              onClick={({ key }) => setSettingsCategory(key)}
              items={[
                { key: 'agents', label: 'agent' },
                { key: 'judge', label: 'Judge 选择' },
                { key: 'mcp', label: 'MCP' },
                { key: 'skills', label: 'Skills' },
              ]}
              style={{ border: 'none', background: 'transparent' }}
            />
          </div>

          {/* 右侧详情面板 */}
          <div
            style={{
              background: '#fff',
              border: '1px solid #e5e7eb',
              borderRadius: 18,
              flex: 1,
              minWidth: 0,
              overflowY: 'auto',
              padding: 18,
            }}
          >
            {settingsCategory === 'agents' && (
              <>
                {Object.keys(drafts).length > 0 ? (
                  <>
                    <div
                      style={{
                        alignItems: 'center',
                        display: 'flex',
                        gap: 8,
                        marginBottom: 16,
                        paddingBottom: 12,
                        borderBottom: '1px solid #eef2f7',
                      }}
                    >
                      <Select
                        style={{ flex: 1 }}
                        value={activeAgentName ?? undefined}
                        onChange={(key) => switchTab(key)}
                        options={Object.values(drafts).map((d) => ({
                          value: d.name,
                          label: (
                            <span style={{ color: 'rgba(15, 23, 42, 0.92)', fontWeight: 500 }}>
                              {d.displayName || d.name}
                            </span>
                          ),
                        }))}
                      />
                      <Button icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                        新增
                      </Button>
                      {Object.keys(drafts).length > 1 && activeAgentName && (
                        <Button
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => {
                            const draft = drafts[activeAgentName];
                            const label = draft?.displayName || activeAgentName;
                            Modal.confirm({
                              title: `确认删除数字员工 ${label}`,
                              content: '该操作不可恢复 请确保没有进行中的对话依赖此 agent',
                              okText: '删除',
                              okButtonProps: { danger: true },
                              cancelText: '取消',
                              onOk: async () => { await removeAgent(activeAgentName); },
                            });
                          }}
                        >
                          删除
                        </Button>
                      )}
                    </div>
                    {activeAgentName && drafts[activeAgentName] && (
                      <AgentForm
                        draft={drafts[activeAgentName]}
                        saving={saving}
                        onPatch={(patch) => setDraftField(activeAgentName, patch)}
                        onSave={() => save(activeAgentName)}
                        onReset={() => reset(activeAgentName)}
                        onAvatarUpload={(file) => uploadAvatar(activeAgentName, file)}
                        onAvatarRemove={() => removeAvatar(activeAgentName)}
                      />
                    )}
                  </>
                ) : (
                  <div
                    style={{
                      background: '#f8fafc',
                      border: '1px dashed #cbd5e1',
                      borderRadius: 16,
                      padding: 28,
                      textAlign: 'center',
                    }}
                  >
                    <Paragraph type="secondary">暂无数字员工，点下方按钮新建一个</Paragraph>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                      新增数字员工
                    </Button>
                  </div>
                )}
              </>
            )}
            {settingsCategory === 'mcp' && <McpSettingsPanel />}

            {settingsCategory === 'skills' && <SkillsPanel />}

            {settingsCategory === 'judge' && (
              <div
                style={{
                  background: '#fff',
                  border: '1px solid #e5e7eb',
                  borderRadius: 18,
                  boxShadow: '0 10px 24px rgba(15, 23, 42, 0.04)',
                  padding: 18,
                }}
              >
                <Text strong style={{ fontSize: 16 }}>Judge 选择</Text>
                <Paragraph type="secondary" style={{ marginTop: 6 }}>
                  选中即生效，由该 agent 负责自动决策。
                </Paragraph>
                {Object.values(drafts).length === 0 ? (
                  <Text type="secondary">暂无候选</Text>
                ) : (
                  <Radio.Group
                    value={judgeTarget ?? undefined}
                    onChange={(e) => setJudge(String(e.target.value))}
                    disabled={saving || loading}
                    style={{ marginTop: 12 }}
                  >
                    <Space direction="vertical" size="middle">
                      {Object.values(drafts).map((d) => (
                        <Radio key={d.name} value={d.name}>
                          <span style={{ color: 'rgba(15, 23, 42, 0.92)', fontWeight: 500 }}>
                            {d.displayName || d.name}
                          </span>
                        </Radio>
                      ))}
                    </Space>
                  </Radio.Group>
                )}
              </div>
            )}
          </div>
        </div>
      </Spin>

      <CreateAgentModal
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onSubmit={handleCreate}
        {...initialCreate}
      />
    </Drawer>
  );
}

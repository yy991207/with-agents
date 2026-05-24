// Skills 配置面板: 以表格形式管理已安装的 skill
// 后端 API: GET/POST /api/skills, PUT/DELETE /api/skills/{name}, PUT /api/skills/{name}/toggle
// POST /api/skills/reload 重载 agent 使 skills 变更生效
import { useEffect, useState, useCallback } from 'react';
import {
  Button,
  Input,
  Modal,
  Space,
  Switch,
  Table,
  Typography,
  message,
} from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import type { SkillView, SkillEditDraft } from '../state/types';
import {
  listSkills,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkill,
  reloadAgents,
} from '../api/http';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

const DEFAULT_SKILLS: SkillView[] = [
  {
    name: 'brainstorming',
    description: '在创建功能、构建组件、添加功能或修改行为之前必须使用',
    content: `# Brainstorming Ideas Into Designs

## Overview
Help turn ideas into fully formed designs and specs through natural collaborative dialogue.

## Key Principles
- One question at a time
- YAGNI ruthlessly
- Explore alternatives before settling
- Incremental validation`,
    enabled: false,
  },
];

export default function SkillsPanel() {
  const [skills, setSkills] = useState<SkillEditDraft[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillEditDraft | null>(null);
  const [editingOriginal, setEditingOriginal] = useState<SkillEditDraft | null>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listSkills();
      const list = resp.skills ?? [];
      if (list.length > 0) {
        setSkills(list.map((s) => ({
          name: s.name,
          description: s.description || '',
          content: s.content,
          enabled: s.enabled,
          dirty: false,
          isNew: false,
        })));
      } else {
        setSkills([]);
      }
    } catch (e) {
      message.error(`加载 skills 配置失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSkills(); }, []);

  const handleAdd = () => {
    setEditingSkill({ name: '', description: '', content: '', enabled: true, dirty: true, isNew: true });
    setEditingOriginal(null);
    setEditOpen(true);
  };

  const handleEdit = (record: SkillEditDraft) => {
    setEditingSkill({ ...record });
    setEditingOriginal(record);
    setEditOpen(true);
  };

  const handleFieldChange = (field: keyof SkillEditDraft, value: string | boolean) => {
    if (!editingSkill) return;
    setEditingSkill({ ...editingSkill, [field]: value, dirty: true });
  };

  const handleEditOk = async () => {
    if (!editingSkill) return;
    const trimmed = editingSkill.name.trim();
    if (!trimmed) { message.warning('skill 名称不能为空'); return; }
    if (!editingSkill.content.trim()) { message.warning('skill 内容不能为空'); return; }

    const duplicate = skills.find((s) => s.name === trimmed && s !== editingOriginal);
    if (duplicate) { message.warning(`已存在同名 skill: ${trimmed}`); return; }

    setSaving(true);
    try {
      if (editingSkill.isNew) {
        await createSkill({ name: trimmed, description: editingSkill.description.trim(), content: editingSkill.content, enabled: editingSkill.enabled });
        message.success(`skill "${trimmed}" 已创建`);
      } else {
        await updateSkill(editingOriginal!.name, { description: editingSkill.description.trim(), content: editingSkill.content, enabled: editingSkill.enabled });
        message.success(`skill "${trimmed}" 已更新`);
      }
      setEditOpen(false);
      setEditingSkill(null);
      setEditingOriginal(null);
      await loadSkills();
    } catch (e) {
      message.error(`保存失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (record: SkillEditDraft) => {
    Modal.confirm({
      title: `确认删除 skill ${record.name}`,
      content: '删除后该 skill 不再注入到 agent 的 system prompt',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        try { await deleteSkill(record.name); message.success(`skill "${record.name}" 已删除`); await loadSkills(); }
        catch (e) { message.error(`删除失败:${e instanceof Error ? e.message : String(e)}`); }
      },
    });
  };

  const handleToggle = async (record: SkillEditDraft, checked: boolean) => {
    try {
      await toggleSkill(record.name, { enabled: checked });
      setSkills((prev) => prev.map((s) => (s.name === record.name ? { ...s, enabled: checked } : s)));
      message.success(`skill "${record.name}" 已${checked ? '启用' : '禁用'}`);
    } catch (e) {
      message.error(`操作失败:${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleReload = async () => {
    try {
      const resp = await reloadAgents();
      message.success(`已重载 ${resp.reloaded} 个 agent，skills 已生效`);
    } catch (e) {
      message.error(`重载失败:${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleInstallDefaults = async () => {
    setSaving(true);
    try {
      for (const s of DEFAULT_SKILLS) {
        try { await createSkill(s); } catch (e) {
          const msg = e instanceof Error ? e.message : '';
          if (!msg.includes('409')) message.warning(`安装 ${s.name} 失败: ${msg}`);
        }
      }
      message.success('默认 skills 已安装');
      await loadSkills();
    } catch (e) {
      message.error(`安装失败:${e instanceof Error ? e.message : String(e)}`);
    } finally { setSaving(false); }
  };

  const columns = [
    { title: '名称', dataIndex: 'name' as const, key: 'name', width: 200, render: (name: string) => <Text strong>{name}</Text> },
    { title: '描述', dataIndex: 'description' as const, key: 'description', ellipsis: true },
    {
      title: '状态', dataIndex: 'enabled' as const, key: 'enabled', width: 80,
      render: (enabled: boolean, record: SkillEditDraft) => (
        <Switch size="small" checked={enabled} onChange={(v) => handleToggle(record, v)} />
      ),
    },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_: unknown, record: SkillEditDraft) => (
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
        <Title level={5} style={{ margin: 0 }}>Skills 配置</Title>
        <Space>
          {skills.length === 0 && !loading && (
            <Button onClick={handleInstallDefaults} loading={saving}>安装默认 Skills</Button>
          )}
          <Button icon={<PlusOutlined />} onClick={handleAdd} disabled={saving || loading}>新增 Skill</Button>
        </Space>
      </div>
      <Paragraph type="secondary" style={{ marginTop: 4 }}>
        管理 agent 的技能模块(skill)。每个 skill 是一段可复用的提示词片段，定义特定场景下的标准操作流程。
        已启用的 skill 会注入到所有 reply agent 的 system prompt 中。
      </Paragraph>
      <div style={{ marginBottom: 12 }}>
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>修改 skills 配置后需点击"重载应用"才会对模型生效</Text>
          <Button icon={<ReloadOutlined />} size="small" onClick={handleReload}>重载应用</Button>
        </Space>
      </div>

      <Table<SkillEditDraft>
        columns={columns} dataSource={skills} rowKey="name"
        loading={loading} size="small" pagination={false}
        style={{ marginBottom: 12 }}
        locale={{ emptyText: '暂无 skill，点击"新增 Skill"添加或点击"安装默认 Skills"' }}
      />

      <Modal
        title={editingSkill && !editingSkill.isNew ? `编辑 Skill: ${editingSkill.name}` : '新增 Skill'}
        open={editOpen}
        onCancel={() => { setEditOpen(false); setEditingSkill(null); setEditingOriginal(null); }}
        onOk={handleEditOk} okText="确定" cancelText="取消" confirmLoading={saving} destroyOnClose width={600}
      >
        {editingSkill && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>名称 (英文标识 不可与其他 skill 重复)</Text>
              <Input value={editingSkill.name} placeholder="如 brainstorming / systematic-debugging"
                onChange={(e) => handleFieldChange('name', e.target.value)} maxLength={64} />
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>描述 (一句话说明 skill 的作用 列表展示用)</Text>
              <Input value={editingSkill.description} placeholder="一句话描述"
                onChange={(e) => handleFieldChange('description', e.target.value)} maxLength={200} />
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>内容 (SKILL.md 完整正文 支持 Markdown 格式)</Text>
              <TextArea value={editingSkill.content} rows={14} placeholder="# Skill Title ..."
                style={{ fontFamily: 'Menlo, Monaco, "Courier New", monospace', fontSize: 13 }}
                onChange={(e) => handleFieldChange('content', e.target.value)} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Switch checked={editingSkill.enabled} onChange={(v) => handleFieldChange('enabled', v)} />
              <Text>启用（关闭后该 skill 不会注入 system prompt）</Text>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
// Skills 配置面板: 以表格形式管理已安装的 skill
// 后端 API: GET/POST /api/skills, PUT/DELETE /api/skills/{name}, PUT /api/skills/{name}/toggle
// POST /api/skills/{name}/files 上传文件包, GET/DELETE /api/skills/{name}/files/{path}
// POST /api/skills/reload 重载 agent 使 skills 变更生效
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Badge,
  Button,
  Input,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  Progress,
} from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, ReloadOutlined, UploadOutlined, PaperClipOutlined, FolderOpenOutlined } from '@ant-design/icons';
import type { SkillView, SkillEditDraft, SkillFileMeta } from '../state/types';
import {
  listSkills,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkill,
  reloadAgents,
  uploadSkillFiles,
  deleteSkillFile,
} from '../api/http';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

/** 解析 SKILL.md 的 YAML frontmatter 返回 { name, description, body }
 *  格式: ---\nname: xxx\ndescription: xxx\n---\n正文...
 *  如果没有 frontmatter 则 name/description 为空 body 为原文
 */
function parseSkillFrontmatter(content: string): { name: string; description: string; body: string } {
  if (!content.startsWith('---')) return { name: '', description: '', body: content };
  const parts = content.split('---', 3);
  if (parts.length < 3) return { name: '', description: '', body: content };
  const metaText = parts[1];
  let name = '';
  let description = '';
  const nameMatch = metaText.match(/name:\s*(.+)/);
  if (nameMatch) name = nameMatch[1].trim();
  const descMatch = metaText.match(/description:\s*(.+)/);
  if (descMatch) description = descMatch[1].trim();
  const body = parts[2].trim();
  return { name, description, body };
}

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
  const [reloading, setReloading] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillEditDraft | null>(null);
  const [editingOriginal, setEditingOriginal] = useState<SkillEditDraft | null>(null);

  // 导入 Skill 包状态
  const [importing, setImporting] = useState(false);
  const [importProgress, setImportProgress] = useState(0);
  const importInputRef = useRef<HTMLInputElement>(null);

  // 编辑模式中上传文件的 input ref
  const editFileInputRef = useRef<HTMLInputElement>(null);

  // 挂载时给导入 input 设置 webkitdirectory 属性(支持选择目录) 绕过 TS 类型限制
  useEffect(() => {
    const importInput = importInputRef.current;
    if (importInput) {
      importInput.setAttribute('webkitdirectory', '');
      importInput.setAttribute('directory', '');
    }
  }, []);

  // Modal 打开时给编辑模式的文件上传 input 设置属性
  useEffect(() => {
    const editInput = editFileInputRef.current;
    if (editInput && editOpen) {
      editInput.setAttribute('webkitdirectory', '');
      editInput.setAttribute('directory', '');
    }
  }, [editOpen]);

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
          files: s.files || [],
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

  // ========== 导入 Skill 包: 一键选择目录 → 自动解析 → 创建 + 上传 ==========

  const handleImportPackage = async () => {
    // 触发目录选择
    importInputRef.current?.click();
  };

  const handleImportInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;

    setImporting(true);
    setImportProgress(10);

    try {
      // 1. 从文件包中提取 SKILL.md 和其他文件
      const filesToUpload: File[] = [];
      const pathsToUpload: string[] = [];
      let skillMdContent: string | null = null;
      let skillName: string = '';

      for (let i = 0; i < fileList.length; i++) {
        const file = fileList[i];
        const relativePath = file.webkitRelativePath
          ? file.webkitRelativePath.split('/').slice(1).join('/')
          : file.name;
        if (!relativePath) continue;

        // 自动解析 SKILL.md
        if (relativePath === 'SKILL.md') {
          try {
            skillMdContent = await file.text();
            const parsed = parseSkillFrontmatter(skillMdContent);
            if (parsed.name) skillName = parsed.name;
          } catch {
            // 读取失败不阻断流程
          }
        }

        // SKILL.md 不再作为附带文件上传 它的内容直接存到 SkillConfig.content
        if (relativePath !== 'SKILL.md') {
          filesToUpload.push(file);
          pathsToUpload.push(relativePath);
        }
      }

      // 2. 确定 skill 名称: SKILL.md frontmatter > 目录名 > 第一个文件名
      if (!skillName && fileList.length > 0) {
        const topDir = fileList[0].webkitRelativePath.split('/')[0];
        skillName = topDir || 'imported-skill';
      }
      if (!skillName) {
        message.error('无法确定 skill 名称');
        return;
      }

      // 3. 检查是否已存在同名 skill
      const duplicate = skills.find((s) => s.name === skillName);
      if (duplicate) {
        message.warning(`已存在同名 skill: ${skillName}，请先删除或使用其他名称`);
        return;
      }

      setImportProgress(30);

      // 4. 解析 SKILL.md 内容
      let content = '# Imported Skill\n\n请手动补充 skill 内容';
      let description = '';
      if (skillMdContent) {
        const parsed = parseSkillFrontmatter(skillMdContent);
        content = parsed.body || content;
        description = parsed.description || '';
      }

      // 5. 创建 skill (先入库)
      setImportProgress(50);
      await createSkill({
        name: skillName,
        description,
        content,
        enabled: true,
      });
      message.success(`skill "${skillName}" 已创建`);

      // 6. 上传附带文件到已有 skill
      if (filesToUpload.length > 0) {
        setImportProgress(70);
        await uploadSkillFiles(skillName, filesToUpload, pathsToUpload);
        message.success(`${filesToUpload.length} 个文件已上传`);
      }

      setImportProgress(100);
      message.success(`Skill 包 "${skillName}" 导入完成`);
      await loadSkills();
    } catch (e) {
      message.error(`导入失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setImporting(false);
      setImportProgress(0);
      // reset input 允许重复选择
      e.target.value = '';
    }
  };

  // ========== 基本操作 ==========

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
      content: '删除后该 skill 不再注入到 agent 的 system prompt，附带文件也会被清理',
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
    setReloading(true);
    try {
      const resp = await reloadAgents();
      message.success(`已重载 ${resp.reloaded} 个 agent，skills 已生效`);
    } catch (e) {
      message.error(`重载失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setReloading(false);
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

  // ========== 编辑模式中上传文件 ==========

  const handleEditFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!editingSkill) return;
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;

    const filesToUpload: File[] = [];
    const pathsToUpload: string[] = [];

    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i];
      const relativePath = file.webkitRelativePath
        ? file.webkitRelativePath.split('/').slice(1).join('/')
        : file.name;
      if (!relativePath) continue;
      filesToUpload.push(file);
      pathsToUpload.push(relativePath);
    }
    if (filesToUpload.length === 0) return;

    setSaving(true);
    try {
      const result = await uploadSkillFiles(editingSkill.name, filesToUpload, pathsToUpload);
      message.success(`${filesToUpload.length} 个文件已上传`);
      setEditingSkill({ ...editingSkill, files: result.files || [], dirty: true });
    } catch (err) {
      message.error(`文件上传失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
      e.target.value = '';
    }
  };

  const handleEditFileDelete = async (filePath: string) => {
    if (!editingSkill) return;
    try {
      await deleteSkillFile(editingSkill.name, filePath);
      message.success(`文件 ${filePath} 已删除`);
      const resp = await listSkills();
      const updated = resp.skills.find((s) => s.name === editingSkill.name);
      if (updated) {
        setEditingSkill({ ...editingSkill, files: updated.files || [], dirty: true });
      }
    } catch (e) {
      message.error(`删除文件失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // ========== 渲染 ==========

  const renderDescription = (text: string) => {
    const safe = text || '';
    const truncated = safe.length > 10 ? safe.slice(0, 10) + '...' : safe;
    return (
      <Tooltip title={safe} placement="topLeft">
        <span>{truncated}</span>
      </Tooltip>
    );
  };

  const columns = [
    { title: '名称', dataIndex: 'name' as const, key: 'name', width: 140, render: (name: string) => <Text strong>{name}</Text> },
    {
      title: '描述', dataIndex: 'description' as const, key: 'description',
      width: 140,
      render: renderDescription,
    },
    {
      title: '文件', dataIndex: 'files' as const, key: 'files', width: 60,
      render: (files: SkillFileMeta[]) => {
        const count = files?.length ?? 0;
        if (count === 0) return <Text type="secondary">-</Text>;
        return <Badge count={count} size="small" color="blue" />;
      },
    },
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
          <Tooltip title="编辑">
            <Button
              aria-label={`编辑 skill ${record.name}`}
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => handleEdit(record)}
            />
          </Tooltip>
          <Tooltip title="删除">
            <Button
              aria-label={`删除 skill ${record.name}`}
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(record)}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div>
      {/* 隐藏的导入 input 支持 webkitdirectory 选择目录 */}
      <input
        ref={importInputRef}
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={handleImportInputChange}
      />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Title level={5} style={{ margin: 0 }}>Skills 配置</Title>
        <Space>
          {skills.length === 0 && !loading && (
            <Button onClick={handleInstallDefaults} loading={saving}>安装默认 Skills</Button>
          )}
          {/* 导入 Skill 包按钮: 一键选择目录完成创建+上传+解析 */}
          <Tooltip title="导入 Skill 包(选择目录自动解析)">
            <Button
              aria-label="导入 Skill 包"
              icon={<FolderOpenOutlined />}
              loading={importing}
              disabled={saving || loading || importing}
              onClick={handleImportPackage}
            >
              导入 Skill 包
            </Button>
          </Tooltip>
          {importing && <Progress percent={importProgress} size="small" style={{ width: 80 }} />}
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
          <Tooltip title="手动新增 Skill">
            <Button
              aria-label="手动新增 Skill"
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
        管理 agent 的技能模块(skill)。点击"导入 Skill 包"可选择完整目录一键导入(SKILL.md 自动解析，目录结构完整保留)。
        已启用的 skill 会注入到所有 reply agent 的 system prompt 中。
      </Paragraph>
      <div style={{ marginBottom: 12 }}>
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>修改 skills 配置后需点击"重载应用"才会对模型生效</Text>
        </Space>
      </div>

      <Table<SkillEditDraft>
        columns={columns} dataSource={skills} rowKey="name"
        loading={loading} size="small" pagination={false}
        scroll={{ x: 'max-content' }}
        style={{ marginBottom: 12, width: 'fit-content', maxWidth: '100%' }}
        locale={{ emptyText: '暂无 skill，点击"导入 Skill 包"或"新增 Skill"添加' }}
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
            {/* 编辑已有 skill 时: 展示文件管理区域 */}
            {!editingSkill.isNew && editingSkill.name.trim() && (
              <div>
                <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: 'block' }}>
                  附带文件 (Python 脚本等，目录结构完整保留)
                </Text>
                {/* 已上传文件列表 */}
                {editingSkill.files && editingSkill.files.length > 0 && (
                  <div style={{ marginBottom: 8, maxHeight: 120, overflowY: 'auto', border: '1px solid #d9d9d9', borderRadius: 6, padding: 8 }}>
                    {editingSkill.files.map((f) => (
                      <div key={f.path} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '2px 0' }}>
                        <Space size="small">
                          <PaperClipOutlined style={{ color: '#8c8c8c' }} />
                          <Text style={{ fontSize: 12 }}>{f.path}</Text>
                          <Tag color={f.path.endsWith('.py') ? 'green' : 'default'} style={{ fontSize: 11 }}>
                            {(f.size / 1024).toFixed(1)}KB
                          </Tag>
                        </Space>
                        <Button
                          type="link" size="small" danger icon={<DeleteOutlined />}
                          onClick={() => handleEditFileDelete(f.path)}
                        />
                      </div>
                    ))}
                  </div>
                )}
                {/* 上传按钮 */}
                <input
                  ref={editFileInputRef}
                  type="file"
                  multiple
                  style={{ display: 'none' }}
                  onChange={handleEditFileUpload}
                />
                <Button
                  icon={<UploadOutlined />}
                  loading={saving}
                  onClick={() => editFileInputRef.current?.click()}
                >
                  上传文件包
                </Button>
              </div>
            )}
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
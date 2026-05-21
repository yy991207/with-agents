// 配置抽屉:在右侧滑出 让用户编辑 4 个 agent 的 model 与 prompt 并选择 judge 指针
// 保存即时生效 不需要重启服务
import { useMemo } from 'react';
import {
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  Radio,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { TabsProps } from 'antd';
import { useSettings } from '../hooks/useSettings';
import { agentColors } from '../theme/tokens';
import type { AgentEditDraft, AgentName } from '../state/types';

const { Paragraph, Text } = Typography;

// 4 个 agent 的固定顺序,保证 tab 顺序与配色一致
const AGENT_ORDER: AgentName[] = ['DeepSeek', 'GLM', 'Kimi', 'Qwen'];

// 单个 agent 的 tab 内容
interface AgentFormProps {
  draft: AgentEditDraft;
  saving: boolean;
  onChange: (field: 'model' | 'prompt', value: string) => void;
  onSave: () => void;
  onReset: () => void;
}

function AgentForm({ draft, saving, onChange, onSave, onReset }: AgentFormProps) {
  return (
    <Form layout="vertical" disabled={saving}>
      <Space style={{ marginBottom: 12 }} size="small" wrap>
        <Tag color="blue">v{draft.version}</Tag>
        {draft.dirty ? <Tag color="orange">未保存</Tag> : <Tag>已同步</Tag>}
      </Space>

      <Form.Item label="模型 ID" required>
        <Input
          value={draft.model}
          placeholder="如 deepseek-v4-pro"
          onChange={(e) => onChange('model', e.target.value)}
          allowClear
        />
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

// 抽屉主体
export default function SettingsDrawer() {
  const { state, closeDrawer, updateDraft, save, reset, setJudge } = useSettings();
  const { open, loading, saving, drafts, judgeTarget } = state;

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
            onChange={(field, value) => updateDraft(name, field, value)}
            onSave={() => save(name)}
            onReset={() => reset(name)}
          />
        ),
      };
    });
  }, [drafts, saving, updateDraft, save, reset]);

  return (
    <Drawer
      title="配置管理"
      placement="right"
      width={720}
      open={open}
      onClose={closeDrawer}
      destroyOnClose={false}
    >
      <Paragraph type="secondary" style={{ marginTop: 0 }}>
        修改 model 与 prompt 后保存立即生效 不需要重启服务
      </Paragraph>

      <Spin spinning={loading} tip="加载配置中…">
        {items && items.length > 0 ? (
          <Tabs items={items} />
        ) : (
          !loading && <Text type="secondary">暂无可编辑的 agent</Text>
        )}

        <Divider style={{ margin: '24px 0 16px' }} />

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

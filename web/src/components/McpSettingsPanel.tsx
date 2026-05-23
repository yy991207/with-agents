// MCP 配置面板: 以 JSON 文本直接编辑 格式对齐 mcp_settings.json
// 整体结构: {"mcpServers": {"name": {"command":"npx","args":[...],"env":{},"alwaysAllow":[...],"disabled":false}}}
import { useEffect, useState, useCallback } from 'react';
import { Button, Input, Space, Typography, message } from 'antd';
import { getMcpConfig, putMcpConfig } from '../api/http';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

// 预填的默认 JSON 模板 前端首次没有配置时提供参考
const DEFAULT_JSON = `{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest", "--extension"],
      "env": {
        "PLAYWRIGHT_MCP_EXTENSION_TOKEN": ""
      },
      "alwaysAllow": [
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_take_screenshot"
      ],
      "disabled": false
    }
  }
}`;

export default function McpSettingsPanel() {
  const [jsonText, setJsonText] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [unsaved, setUnsaved] = useState(false);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getMcpConfig();
      const config = resp.config;
      if (config && Object.keys(config).length > 0) {
        setJsonText(JSON.stringify(config, null, 2));
      } else {
        // 新环境 提供默认模板
        setJsonText(DEFAULT_JSON.trim());
      }
      setUnsaved(false);
    } catch (e) {
      message.error(
        `加载 MCP 配置失败:${e instanceof Error ? e.message : String(e)}`,
      );
      setJsonText(DEFAULT_JSON.trim());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSave = async () => {
    // 先试解析 JSON 确保合法
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(jsonText);
      if (typeof parsed !== 'object' || Array.isArray(parsed)) {
        message.error('JSON 必须是对象类型');
        return;
      }
    } catch {
      message.error('JSON 格式不正确 请检查语法');
      return;
    }

    setSaving(true);
    try {
      await putMcpConfig({ config: parsed });
      setJsonText(JSON.stringify(parsed, null, 2));
      setUnsaved(false);
      message.success('MCP 配置已保存');
    } catch (e) {
      message.error(
        `保存失败:${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    loadConfig();
  };

  const handleChange = (value: string) => {
    setJsonText(value);
    setUnsaved(true);
  };

  return (
    <div>
      <Title level={5} style={{ marginTop: 0 }}>
        MCP 配置
      </Title>
      <Paragraph type="secondary">
        直接编辑 JSON 配置完成 MCP 服务器的增删改。格式参考 Roo Code 的
        mcp_settings.json 结构。保存后即刻生效。
      </Paragraph>

      <div style={{ marginBottom: 12 }}>
        <TextArea
          value={jsonText}
          onChange={(e) => handleChange(e.target.value)}
          rows={22}
          style={{
            fontFamily: 'Menlo, Monaco, "Courier New", monospace',
            fontSize: 13,
            lineHeight: 1.6,
          }}
          placeholder={DEFAULT_JSON.trim()}
          disabled={saving || loading}
        />
      </div>

      <Space>
        <Button type="primary" onClick={handleSave} loading={saving} disabled={!unsaved}>
          保存
        </Button>
        <Button onClick={handleCancel} disabled={!unsaved || saving}>
          取消
        </Button>
        {unsaved && (
          <Text type="warning" style={{ fontSize: 12 }}>
            有未保存的改动
          </Text>
        )}
        {!unsaved && !loading && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            已同步至数据库
          </Text>
        )}
      </Space>
    </div>
  );
}
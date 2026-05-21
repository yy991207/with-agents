// antd 主题 token 与 agent 颜色:不允许紫色渐变
import type { ThemeConfig } from 'antd';
import type { AgentName } from '../state/types';

// 4 个 agent 的代表色,组件里用作头像背景、卡片左侧条等
export const agentColors: Record<AgentName, string> = {
  DeepSeek: '#1565c0', // 蓝
  GLM: '#e65100', // 橙
  Kimi: '#c62828', // 红
  Qwen: '#00838f', // 青
};

// antd 5 ConfigProvider 主题配置
// 主色定为 DeepSeek 蓝,圆角与基础字号也在这里集中
export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: agentColors.DeepSeek,
    borderRadius: 8,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
  },
  components: {
    Layout: {
      headerBg: '#ffffff',
      siderBg: '#ffffff',
      bodyBg: '#f5f7fa',
    },
    Button: {
      borderRadius: 8,
    },
  },
};

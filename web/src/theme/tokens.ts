// antd 主题 token 与 agent 颜色:不允许紫色渐变 单色十六进制保留兜底
import type { ThemeConfig } from 'antd';

// agent 颜色调色盘:覆盖蓝/橙/红/深蓝/青/紫/玫红 共 7 个色相
// 单色紫 #6a1b9a 留作兜底 不构成渐变
const PALETTE: readonly string[] = [
  '#1565c0', // 蓝
  '#e65100', // 橙
  '#c62828', // 红
  '#283593', // 深蓝
  '#00838f', // 青
  '#6a1b9a', // 紫(单色 兜底)
  '#ad1457', // 玫红
];

// 根据 agent 名做稳定 hash 在调色盘里取色
// 同一个 name 永远命中同一个色 不依赖外部 mapping
export function getAgentColor(name: string): string {
  if (!name) return PALETTE[0];
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return PALETTE[h % PALETTE.length];
}

// antd 5 ConfigProvider 主题配置
// 主色用调色盘第一个蓝色，同时补齐背景、边框和文本层级，后续 LobeHub 化页面统一复用
export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: PALETTE[0],
    colorBgBase: '#f3f5f8',
    colorBgLayout: '#f3f5f8',
    colorBgContainer: '#ffffff',
    colorBorder: '#e5e7eb',
    colorText: 'rgba(15, 23, 42, 0.92)',
    colorTextSecondary: 'rgba(51, 65, 85, 0.72)',
    colorTextTertiary: 'rgba(71, 85, 105, 0.56)',
    borderRadius: 12,
    borderRadiusLG: 16,
    controlHeight: 36,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
    boxShadowSecondary: '0 12px 32px rgba(15, 23, 42, 0.08)',
  },
  components: {
    Layout: {
      headerBg: '#ffffff',
      siderBg: '#ffffff',
      bodyBg: '#f3f5f8',
    },
    Button: {
      borderRadius: 10,
      controlHeight: 36,
    },
    Card: {
      borderRadiusLG: 16,
    },
    Input: {
      borderRadius: 12,
      controlHeight: 40,
    },
  },
};

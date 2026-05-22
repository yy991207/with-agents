// 应用入口:挂载 React,注入 antd ConfigProvider 与 Chat Provider
import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import 'antd/dist/reset.css';
// 代码块高亮主题:在顶层 import 一次即可,所有 ReactMarkdown 实例共用
import 'highlight.js/styles/github.css';
import App from './App';
import { ChatProvider } from './state/ChatContext';
import { themeConfig } from './theme/tokens';
import './styles/global.css';

const rootEl = document.getElementById('root');
if (!rootEl) {
  throw new Error('找不到 #root 节点');
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <ConfigProvider theme={themeConfig}>
      <ChatProvider>
        <App />
      </ChatProvider>
    </ConfigProvider>
  </React.StrictMode>,
);

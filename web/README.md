# Multi-LLM Chat Web

think-then-choose 模式的多模型对话前端,基于 Vite + React 18 + TypeScript + antd 5。

## 环境要求

- Node.js >= 18
- 后端服务运行在 `http://localhost:8002`(由项目根 backend 提供)

## 常用命令

```bash
# 安装依赖
npm install

# 启动开发服务器(默认 5173 端口,自动代理到后端 8002)
npm run dev

# 构建生产包(先做 TypeScript 类型检查再 vite build)
npm run build

# 仅做 TS 类型检查,不打包
npm run type-check

# 预览生产构建
npm run preview
```

## 目录约定

- `src/api`:HTTP 与 SSE 客户端
- `src/state`:基于 useReducer + Context 的全局状态
- `src/components`:UI 组件
- `src/hooks`:任务、会话相关的封装 hook
- `src/theme`:antd token 与 agent 颜色
- `src/styles`:少量全局样式

## 接口代理

dev 模式下 Vite 把以下路径转发到后端:

- `POST /ask` `/decide` `/cancel` `/retry-think`
- `GET /history/:sessionId` `/sessions`
- `GET /sse/:taskId`(SSE 长连接,关闭 ws 透传)

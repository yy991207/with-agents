import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

// 后端服务地址,FastAPI 默认监听 8002
const BACKEND = 'http://localhost:8002';

// 需要转发到后端的 JSON 接口路径前缀
const apiPaths = ['/ask', '/decide', '/cancel', '/retry-think', '/history', '/sessions', '/api'];

// 构造 dev proxy 表
const buildProxy = (): Record<string, ProxyOptions> => {
  const proxy: Record<string, ProxyOptions> = {};
  for (const p of apiPaths) {
    proxy[p] = {
      target: BACKEND,
      changeOrigin: true,
    };
  }
  // SSE 长连接:必须关闭 ws,同源透传 text/event-stream
  proxy['/sse'] = {
    target: BACKEND,
    changeOrigin: true,
    ws: false,
  };
  return proxy;
};

// Vite 配置:5173 端口,React 插件,dev proxy 转发后端接口
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: buildProxy(),
  },
});

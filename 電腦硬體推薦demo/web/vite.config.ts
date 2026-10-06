import { defineConfig } from 'vite';

// 開發模式：前端跑在 5173，/api 轉給 FastAPI（8000）。
// 正式使用時不需要 Node：npm run build 產生的 dist/ 由 FastAPI 直接送出。
export default defineConfig({
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});

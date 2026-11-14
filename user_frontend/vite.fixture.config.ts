import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/** 只服务 fixtures/account-sidebar，不进入 npm run build。 */
export default defineConfig({
  root: fileURLToPath(new URL('./fixtures/account-sidebar', import.meta.url)),
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5199,
    strictPort: true,
  },
})

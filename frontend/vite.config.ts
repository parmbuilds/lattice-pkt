import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// Same origin everywhere (KTD12): the dev server proxies the API and MCP to FastAPI.
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/mcp': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    exclude: ['e2e/**', 'node_modules/**'],
  },
})

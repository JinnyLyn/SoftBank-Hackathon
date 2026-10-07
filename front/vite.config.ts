import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 백엔드 붙일 때 VITE_USE_MOCK=false 로 두고 /api 를 프록시
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})

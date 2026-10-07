import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 운영 서버(nginx, CloudFront 등)에도 같은 헤더를 붙여야 함. README 참고
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' https://cdn.jsdelivr.net",
  "font-src 'self' https://cdn.jsdelivr.net",
  "img-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join('; ')

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Cross-Origin-Opener-Policy': 'same-origin',
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // dev 서버는 HMR이 인라인 스크립트를 써서 CSP는 빼고 나머지만
    headers: SECURITY_HEADERS,
    // 백엔드 붙일 때 VITE_USE_MOCK=false 로 두고 /api 를 프록시
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  preview: {
    headers: { ...SECURITY_HEADERS, 'Content-Security-Policy': CSP },
  },
})

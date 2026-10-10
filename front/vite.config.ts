import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 백엔드 주소. Windows에서 localhost가 IPv6(::1)로 풀려 연결이 안 되는 경우가 있어 127.0.0.1을 기본으로 씀
const API_TARGET = process.env.API_TARGET ?? 'http://127.0.0.1:8000'

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
    // VITE_USE_MOCK=false 일 때 /api 를 백엔드로 넘김. 같은 출처가 되므로 백엔드 CORS 설정이 필요 없음
    proxy: {
      '/api': API_TARGET,
    },
  },
  preview: {
    headers: { ...SECURITY_HEADERS, 'Content-Security-Policy': CSP },
  },
})

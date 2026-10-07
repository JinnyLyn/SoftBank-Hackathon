// 백엔드 호출 공통 처리
// - 인증은 HttpOnly 세션 쿠키로만. 토큰을 JS에서 저장하지 않음
// - 상태를 바꾸는 요청에는 CSRF 토큰 헤더를 붙임
// - 401이면 세션 만료로 보고 앱에 알림

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

let csrfToken = ''
export const setCsrfToken = (t: string) => {
  csrfToken = t
}

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()
export function onUnauthorized(fn: Listener) {
  unauthorizedListeners.add(fn)
  return () => {
    unauthorizedListeners.delete(fn)
  }
}
export const emitUnauthorized = () => unauthorizedListeners.forEach((fn) => fn())

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS'])

export async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!SAFE.has(method) && csrfToken) headers.set('X-CSRF-Token', csrfToken)
  headers.set('X-Requested-With', 'fetch')

  const res = await fetch('/api' + path, { ...init, method, headers, credentials: 'same-origin' })
  if (res.status === 401) {
    emitUnauthorized()
    throw new ApiError(401, '로그인이 만료되었습니다.')
  }
  if (res.status === 403) throw new ApiError(403, '이 작업을 할 권한이 없습니다.')
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${await res.text()}`)
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/**
 * IdP로 보내기 전에 주소 검증 (오픈 리다이렉트 방지).
 * 같은 출처이거나, 허용한 IdP 호스트의 https 주소만 통과.
 */
export function isSafeRedirect(url: string, allowedHosts: string[]) {
  try {
    const u = new URL(url, window.location.origin)
    if (u.origin === window.location.origin) return true
    return u.protocol === 'https:' && allowedHosts.some((h) => u.hostname === h || u.hostname.endsWith('.' + h))
  } catch {
    return false
  }
}

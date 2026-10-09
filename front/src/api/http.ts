// 백엔드 호출 공통 처리

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch('/api' + path, { ...init, credentials: 'same-origin' })
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
 * 외부 주소로 보내기 전에 검증 (오픈 리다이렉트 방지).
 * 같은 출처이거나, 허용한 호스트의 https 주소만 통과.
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

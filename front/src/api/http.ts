// 백엔드 호출 공통 처리

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

// 백엔드 오류 형식: { "error": "설명", "details": [{ loc, msg }] }  (details는 422일 때만)
interface ErrorBody {
  error?: string
  details?: { loc?: (string | number)[]; msg?: string }[]
}

async function toApiError(res: Response): Promise<ApiError> {
  const text = await res.text()
  let message = text || res.statusText
  try {
    const body = JSON.parse(text) as ErrorBody
    if (body.error) {
      const fields = (body.details ?? [])
        .map((d) => [d.loc?.filter((p) => p !== 'body').join('.'), d.msg].filter(Boolean).join(': '))
        .filter(Boolean)
      message = fields.length ? `${body.error} (${fields.join(', ')})` : body.error
    }
  } catch {
    // JSON이 아니면 본문 그대로
  }
  if (res.status === 503) message = `서버가 DB에 연결하지 못했습니다. ${message}`
  return new ApiError(res.status, message)
}

export async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch('/api' + path, { ...init, credentials: 'same-origin' })
  if (!res.ok) throw await toApiError(res)
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/** 404면 null. 아직 만들어지지 않은 결과(분석, 배포 상태)를 기다릴 때 씀 */
export async function reqOrNull<T>(path: string): Promise<T | null> {
  try {
    return await req<T>(path)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null
    throw e
  }
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

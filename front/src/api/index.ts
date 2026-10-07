import type {
  Analysis,
  Choice,
  Connection,
  ConnectionInput,
  DeployRecord,
  DeployStatus,
  Recommendation,
  ScaleInput,
  Session,
  Source,
  SsoDiscovery,
  TerraformBundle,
} from '../types'
import * as mock from './mock'
import { isSafeRedirect, req, send, setCsrfToken } from './http'

export { sourceName } from './mock'
export { ApiError, onUnauthorized } from './http'

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

/** 리다이렉트를 허용할 IdP 호스트. 백엔드가 같은 출처 주소를 주면 비워 둬도 됨 */
const IDP_HOSTS = (import.meta.env.VITE_IDP_HOSTS ?? '').split(',').filter(Boolean)

// 백엔드 엔드포인트는 아직 가안. 확정되면 여기만 고치면 됨
const real = {
  /** 세션 쿠키가 살아 있으면 사용자 정보, 아니면 null */
  async session(): Promise<Session | null> {
    const res = await fetch('/api/auth/session', { credentials: 'same-origin' })
    if (res.status === 401) return null
    if (!res.ok) throw new Error(`${res.status}`)
    return res.json()
  },
  discover: (email: string) => req<SsoDiscovery>('/auth/sso/discover', send('POST', { email })),
  /** IdP 로그인 화면으로 이동. 돌아오면 세션 쿠키가 심어져 있음 */
  startSso(_email: string, d: SsoDiscovery): Promise<Session> {
    if (!isSafeRedirect(d.redirectUrl, IDP_HOSTS)) return Promise.reject(new Error('허용되지 않은 로그인 주소입니다.'))
    window.location.assign(d.redirectUrl)
    return new Promise(() => {})
  },
  logout: () => req<void>('/auth/logout', { method: 'POST' }),

  listConnections: () => req<Connection[]>('/connections'),
  saveConnection: (input: ConnectionInput) =>
    input.id
      ? req<Connection>(`/connections/${input.id}`, send('PUT', input))
      : req<Connection>('/connections', send('POST', input)),
  checkConnection: (id: string) => req<Connection>(`/connections/${id}/check`, { method: 'POST' }),
  deleteConnection: (id: string) => req<void>(`/connections/${id}`, { method: 'DELETE' }),

  analyze(source: Source, scale: ScaleInput) {
    const form = new FormData()
    if (source.kind === 'zip') form.append('file', source.file)
    else {
      form.append('repo_url', source.url)
      form.append('branch', source.branch)
    }
    form.append('expected_users', scale.expectedUsers)
    form.append('traffic_pattern', scale.pattern)
    form.append('purpose', scale.purpose)
    return req<Analysis>('/projects', { method: 'POST', body: form })
  },
  recommend: (projectId: string, scale: ScaleInput) =>
    req<Recommendation>(`/projects/${projectId}/recommend`, send('POST', scale)),
  generate: (projectId: string, choice: Choice) =>
    req<TerraformBundle>(`/projects/${projectId}/code`, send('POST', choice)),
  approve: (projectId: string, choice: Choice) => req<void>(`/projects/${projectId}/deploy`, send('POST', choice)),
  status: (projectId: string) => req<DeployStatus>(`/projects/${projectId}/status`),
  history: () => req<DeployRecord[]>('/deployments'),
}

const mockApi: typeof real = { ...mock, startSso: mock.mockCompleteSso }

export const api: typeof real = USE_MOCK ? mockApi : real

/** 로그인 직후, 세션 확인 직후 호출 */
export const applySession = (s: Session) => setCsrfToken(s.csrfToken)

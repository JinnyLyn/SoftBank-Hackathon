import type {
  Choice,
  Connection,
  ConnectionInput,
  DeployRecord,
  DeployStatus,
  Recommendation,
  ScaleInput,
  TerraformBundle,
} from '../types'
import * as mock from './mock'
import * as backend from './real'
import { req, send } from './http'

export { sourceName } from '../format'
export { ApiError } from './http'

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

/** 화면에 MOCK 표시를 띄울지. 모의 결과를 실제 배포로 오해하지 않게 */
export const IS_MOCK = USE_MOCK

// 백엔드 엔드포인트는 아직 가안. 확정되면 여기만 고치면 됨
const real = {
  listConnections: () => req<Connection[]>('/connections'),
  saveConnection: (input: ConnectionInput) =>
    input.id
      ? req<Connection>(`/connections/${input.id}`, send('PUT', input))
      : req<Connection>('/connections', send('POST', input)),
  checkConnection: (id: string) => req<Connection>(`/connections/${id}/check`, { method: 'POST' }),
  deleteConnection: (id: string) => req<void>(`/connections/${id}`, { method: 'DELETE' }),

  analyze: backend.analyze,
  recommend: (projectId: string, scale: ScaleInput) =>
    req<Recommendation>(`/projects/${projectId}/recommend`, send('POST', scale)),
  generate: (projectId: string, choice: Choice) =>
    req<TerraformBundle>(`/projects/${projectId}/code`, send('POST', choice)),
  approve: (projectId: string, choice: Choice) => req<void>(`/projects/${projectId}/deploy`, send('POST', choice)),
  /** 실패 진단의 수정안을 반영하고 검증·plan을 다시 만듦. 결과는 다시 승인받아야 배포됨 */
  applyFix: (projectId: string, choice: Choice) =>
    req<TerraformBundle>(`/projects/${projectId}/fix`, send('POST', choice)),
  status: (projectId: string) => req<DeployStatus>(`/projects/${projectId}/status`),
  history: () => req<DeployRecord[]>('/deployments'),
}

export const api: typeof real = USE_MOCK ? mock : real


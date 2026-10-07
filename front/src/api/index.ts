import type { Analysis, CostLine, DeployRecord, DeployStatus, PlanSummary, ScaleInput, Target } from '../types'
import * as mock from './mock'

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, init)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// 백엔드 엔드포인트는 아직 가안. 확정되면 여기만 고치면 됨
const real = {
  analyze(file: File, scale: ScaleInput) {
    const form = new FormData()
    form.append('file', file)
    form.append('expected_users', scale.expectedUsers)
    form.append('purpose', scale.purpose)
    return req<Analysis>('/projects', { method: 'POST', body: form })
  },
  estimate: (projectId: string, targets: Target[], scale: ScaleInput) =>
    req<CostLine[]>(`/projects/${projectId}/estimate`, post({ targets, scale })),
  plan: (projectId: string, targets: Target[]) =>
    req<PlanSummary>(`/projects/${projectId}/plan`, post({ targets })),
  approve: (projectId: string, targets: Target[]) =>
    req<void>(`/projects/${projectId}/deploy`, post({ targets })),
  status: (projectId: string) => req<DeployStatus>(`/projects/${projectId}/status`),
  history: () => req<DeployRecord[]>('/deployments'),
}

export const api: typeof real = USE_MOCK ? mock : real

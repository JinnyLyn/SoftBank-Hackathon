import { ApiError, req, send } from '../api/http'
import { toComposeBundle, type PlanOut } from '../api/real'
import type { TerraformBundle } from '../types'

export type ComponentState = 'ready' | 'starting' | 'down' | 'stale' | 'unconfigured'
export interface Readiness {
  status: ComponentState
  checked_at: string | null
  expires_at: string | null
  analysis_mode: 'rules'
  target: 'local_docker'
  components: Record<'api' | 'db' | 'docker' | 'analyzer' | 'worker', { status: ComponentState; phase?: string }>
  reasons: { code: string; message: string; action: string }[]
}
export type ReviewedPlan = Readonly<{ projectId: string; planId: string; fingerprint: string; target: 'local_docker' }>
export interface LocalReview { reference: ReviewedPlan; bundle: TerraformBundle; monthlyUsd: number }

export const readiness = () => req<Readiness>('/onprem/readiness', { timeoutMs: 3000 })

/** No shared provider/cache lookup: each review carries its own approval identity. */
export async function recommendLocal(projectId: string): Promise<LocalReview | null> {
  const plans = await req<PlanOut[]>(`/projects/${encodeURIComponent(projectId)}/plans`)
  const plan = plans.filter((p) => p.project_id === projectId && p.target === 'local_docker' && p.status === 'awaiting_approval')
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  if (!plan) return null
  if (!plan.fingerprint || typeof plan.variables.compose_yaml !== 'string' || !plan.variables.compose_yaml.trim())
    throw new ApiError(409, '검토할 Compose 파일 또는 계획 식별자가 없습니다. 운영자에게 계획 재생성을 요청해 주세요.')
  return {
    reference: Object.freeze({ projectId, planId: plan.id, fingerprint: plan.fingerprint, target: 'local_docker' }),
    bundle: toComposeBundle(plan),
    monthlyUsd: Number(plan.cost_estimate?.amount ?? 0),
  }
}

function assertReviewed(plan: PlanOut, reviewed: ReviewedPlan) {
  if (reviewed.target !== 'local_docker' || plan.target !== reviewed.target || plan.id !== reviewed.planId ||
      plan.project_id !== reviewed.projectId || plan.fingerprint !== reviewed.fingerprint)
    throw new ApiError(409, '검토한 계획의 대상·프로젝트·내용이 바뀌었습니다. 계획을 다시 조회하고 검토해 주세요.')
}

/** Approval retries are explicit; even an existing deployment must match the reviewed identity. */
export async function approveLocal(reviewed: ReviewedPlan): Promise<void> {
  let plan = await req<PlanOut>(`/plans/${encodeURIComponent(reviewed.planId)}`)
  assertReviewed(plan, reviewed)
  const existing = async () => {
    const rows = await req<{ plan_id: string }[]>(`/deployments?${new URLSearchParams({ project_id: reviewed.projectId, limit: '100' })}`)
    return rows.some((row) => row.plan_id === reviewed.planId)
  }
  if (await existing()) return
  if (!plan.terraform_plan_ready) throw new ApiError(409, '계획 파일이 아직 준비되지 않았습니다. 다시 조회해 주세요.')
  if (plan.status === 'awaiting_approval') {
    plan = await req<PlanOut>(`/plans/${encodeURIComponent(reviewed.planId)}/approve`, send('POST', { expected_fingerprint: reviewed.fingerprint }))
    assertReviewed(plan, reviewed)
  }
  if (plan.status !== 'approved') throw new ApiError(409, '더 이상 승인할 수 없는 계획입니다. 계획을 다시 조회해 주세요.')
  try {
    await req('/deployments', send('POST', { plan_id: reviewed.planId, expected_fingerprint: reviewed.fingerprint }))
  } catch (error) {
    if (await existing().catch(() => false)) return
    throw error
  }
}

/** One generation per analysis/review/reset, including repeated requests for the same project. */
export class Operations {
  private generation = 0
  next() { const generation = ++this.generation; return () => generation === this.generation }
  invalidate() { ++this.generation }
}

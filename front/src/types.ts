export type Target = 'aws' | 'onprem'

export type StepKey = 'upload' | 'analyze' | 'approve' | 'build' | 'deploy' | 'health'
export type StepStatus = 'waiting' | 'active' | 'done' | 'failed'

export type ExpectedUsers = '~100' | '~1,000' | '~10,000' | '10,000+'

export interface ScaleInput {
  expectedUsers: ExpectedUsers
  purpose: string
}

export interface Analysis {
  projectId: string
  framework: string
  port: number
  db: string
  fileStorage: string
  /** 사용자에게 꼭 알려야 하는 제안 (예: SQLite -> RDS) */
  notice?: { title: string; detail: string }
  /** 스캐너가 찾은 판단 근거 */
  evidence: string[]
}

export interface CostLine {
  target: Target
  resource: string
  spec: string
  /** 월 예상 비용(USD). null이면 추가 비용 없음 */
  monthlyUsd: number | null
}

export interface PlanSummary {
  add: number
  change: number
  destroy: number
  /** terraform plan 출력 (간추린 것) */
  text: string
}

export interface DeployStatus {
  steps: Record<StepKey, StepStatus>
  urls: Partial<Record<Target, string>>
  /** 실패 시 AI가 로그를 보고 정리한 원인과 수정 방향 */
  diagnosis?: { cause: string; fix: string; log: string }
}

export interface DeployRecord {
  id: string
  app: string
  version: string
  target: Target
  method: string
  status: 'success' | 'failed' | 'running'
  note?: string
  createdAt: string
}

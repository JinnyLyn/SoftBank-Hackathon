// 오래 걸리는 작업(분석, 계획)의 진행 게이지 계산

export interface WorkStage {
  label: string
  state: 'done' | 'current' | 'todo' | 'failed'
  /** 게이지에서 차지하는 비중 (오래 걸리는 단계일수록 크게) */
  weight: number
  /** 보통 걸리는 시간(초). 진행 중일 때 게이지가 이 시간에 맞춰 천천히 참 */
  expectedSec: number
}

// 게이지 비중과 보통 걸리는 시간(초). 계획(Terraform)이 가장 오래 걸림
export const STAGE_SIZE = {
  upload: { weight: 10, expectedSec: 3 },
  analyze: { weight: 30, expectedSec: 12 },
  plan: { weight: 60, expectedSec: 120 },
}

// 진행 중인 단계는 이 비율까지만 참. 서버가 끝났다고 알려 줘야 다음 단계로 넘어감 (실제보다 앞서 가지 않게)
const STAGE_CAP = 0.95

/** 끝난 단계 비중 + 진행 중 단계 비중 × (1 - e^(-경과/보통 시간)), 0~100 */
export function progressPercent(stages: WorkStage[], stageElapsedSec: number): number {
  const total = stages.reduce((s, x) => s + x.weight, 0) || 1
  const done = stages.filter((x) => x.state === 'done').reduce((s, x) => s + x.weight, 0)
  const cur = stages.find((x) => x.state === 'current')
  const part = cur ? cur.weight * Math.min(STAGE_CAP, 1 - Math.exp(-stageElapsedSec / Math.max(1, cur.expectedSec))) : 0
  return Math.round(((done + part) / total) * 100)
}


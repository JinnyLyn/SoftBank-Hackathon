export type Provider = 'aws' | 'onprem'

export type Source =
  | { kind: 'zip'; file: File }
  | { kind: 'github'; url: string; branch: string }

export type ExpectedUsers = '~100' | '~1,000' | '~10,000' | '10,000+'
export type TrafficPattern = 'steady' | 'peak' | 'unknown'

export interface ScaleInput {
  expectedUsers: ExpectedUsers
  pattern: TrafficPattern
  purpose: string
  /** 월 예산 한도(USD). 이 금액을 넘는 구성은 추천하지도, 고르게 하지도 않음 */
  monthlyBudgetUsd: number
}

export interface Finding {
  level: 'info' | 'warn'
  title: string
  detail: string
}

/** Dockerfile이 없을 때 분석기가 만든 초안 (사용자가 저장소에 넣는 제안) */
export interface DockerfileDraft {
  content: string
  port: number
  /** 초안을 만들 때 본 근거 (파일:줄) */
  basedOn: string[]
}

export interface Analysis {
  projectId: string
  /** 코드에서 읽어낸 스택 정보 (프레임워크, 포트 등) */
  stack: { label: string; value: string }[]
  findings: Finding[]
  /** 스캐너가 찾은 판단 근거 */
  evidence: string[]
  /**
   * 배포할 수 없는 이유 (미지원 DB·여러 이미지, 포트·헬스체크·Dockerfile을 못 찾음 등).
   * 하나라도 있으면 worker가 계획을 만들지 않으므로 기다리지 않고 이유를 보여 줌
   */
  blockers?: string[]
  /** 있으면 저장소에 Dockerfile을 넣어야 배포할 수 있음 (worker는 소스 안의 Dockerfile로만 빌드) */
  dockerfileDraft?: DockerfileDraft
}

export type TierKey = 'lean' | 'balanced' | 'roomy'

export interface Resource {
  service: string
  spec: string
  monthlyUsd: number
  /** 왜 이 사양을 골랐는지 */
  why: string
}

export interface Tier {
  key: TierKey
  label: string
  headline: string
  fit: string
  tradeoff: string
  resources: Resource[]
  /** 추가 비용이 없을 때(온프레미스) 대신 보여 줄 자원 사용량 */
  usageNote?: string
  /**
   * 서버의 월 비용 추정 총액. 있으면 추천·선택 차단·승인 화면이 모두 이 값을 씀
   * (리소스별 금액은 참고용이라 합계가 달라도 총액은 이 값)
   */
  totalUsd?: number
  /** 평소(최소 태스크 수) 월 비용. 있으면 부하 최대 비용(totalUsd)과 같이 보여 줌 */
  usualUsd?: number
  /** worker가 매긴 금액 순위: 최저·평균·최대 */
  rankLabel?: string
  /**
   * 이 안의 Terraform 계획이 있는지. false 면 비교용(예상 비용만 있음): 누르면 이 안으로 다시 계획을 요청함
   */
  planned?: boolean
  /** 코드(비용 계산)가 추천한 안인지. 사용자가 다른 안을 골라 다시 계획해도 추천 표시는 이 안에 남음 */
  suggested?: boolean
}

/** 연결된 배포 대상 하나에서 가능한 구성들 */
export interface TargetOption {
  connectionId: string
  provider: Provider
  name: string
  tiers: Tier[]
}

/** 어디에(connection) 어떤 크기로(tier) 올릴지 */
export interface Choice {
  connectionId: string
  tier: TierKey
}

export interface Recommendation {
  /** 예산 안에 맞는 구성이 하나도 없으면 null. reason에 이유를 씀 */
  recommended: Choice | null
  reason: string
  options: TargetOption[]
  assumptions: string[]
  /**
   * 백엔드가 미리 만들어 둔 배포 코드. 키는 `${connectionId}:${tier}`.
   * 최소한 추천 조합은 들어 있어야 코드 검토 화면이 바로 뜸
   */
  bundles?: Record<string, TerraformBundle>
}

export interface Patch {
  file: string
  before: string[]
  after: string[]
}

/** 백엔드 배포 계획(PlanOut)에서 승인에 필요한 정보 */
export interface PlanInfo {
  planId: string
  moduleId: string
  summary: string
  /** 사용자가 승인하는 계획 내용의 식별자. 서버가 만든 값을 그대로 돌려보내야 함 */
  fingerprint: string
  /** AWS는 Terraform plan 파일이 서버에 올라와 있어야 승인 가능 */
  ready: boolean
  pricingAsOf?: string
  /** 계획 금액에 넣지 않은 비용 (worker의 cost.excluded). 총액이 전부를 덮는 것처럼 보이지 않게 승인 화면에 같이 보여 줌 */
  excludedCosts?: string[]
  /** AWS 공용 기반(ALB·RDS 등)의 월 비용. sharedIncluded가 true면 계획 금액에 합쳐져 있고, false면 참고로만 담았을 뿐 계획 금액에 넣지 않음 */
  sharedMonthlyUsd?: number
  sharedIncluded?: boolean
}

export interface TerraformBundle {
  /** 온프레미스는 Terraform 대신 docker compose 사용 */
  tool: 'terraform' | 'compose'
  /** 실패 진단 수정안을 반영해 다시 만든 경우, 이번에 바뀐 내용 (재승인 화면에 표시) */
  patches?: Patch[]
  files: { name: string; content: string }[]
  /** 개수를 알 수 없으면(백엔드가 plan 요약만 줄 때) null */
  plan: { add: number | null; change: number | null; destroy: number | null; text: string }
  /** 실제 백엔드 계획일 때만 있음 */
  planInfo?: PlanInfo
}

// ---------- 도메인 ----------

/** auto: 플랫폼 도메인 아래 자동 주소(서버가 이름을 정함), own: 이미 가진 도메인 연결 */
export type DomainMode = 'auto' | 'own'

export interface DomainChoice {
  mode: DomainMode
  /** own 일 때 연결할 도메인. auto 는 빈 문자열 */
  name: string
}

export interface DnsRecord {
  type: 'CNAME' | 'A' | 'ALIAS' | 'TXT' | 'NS'
  name: string
  value: string
  /** 화면 설명 (예: "인증서 확인용") */
  purpose: string
}

/** 서버가 확정한 도메인 계획. 비용 승인 화면에 앱 비용과 함께 보여 줌 */
export interface DomainPlan {
  /** none: 서버에 도메인 기능이 없어 미리보기 주소(AWS 기본 주소)로만 배포 */
  mode: DomainMode | 'none'
  /** auto 는 서버가 정한 주소, own 은 사용자가 넣은 도메인 */
  name: string | null
  /** 매달 붙는 비용 (DNS 호스팅 등). 없으면 0 */
  monthlyUsd: number
  /** 이미 가진 도메인일 때 사용자가 도메인 업체에 넣을 레코드. 값은 배포 뒤 확정될 수 있음 */
  records: DnsRecord[]
  note?: string
}

export type DomainState = 'skipped' | 'waiting_dns' | 'issuing_cert' | 'active' | 'failed'

export interface DomainStatus {
  state: DomainState
  name: string | null
  message?: string
  /** waiting_dns 일 때 아직 확인되지 않은 레코드 */
  records?: DnsRecord[]
  /** active 일 때 접속 주소 */
  url?: string
}

export interface DeployStatus {
  state: 'running' | 'success' | 'failed'
  /** 도메인 연결 진행 상태. 앱 배포가 끝난 뒤에도 DNS·인증서 때문에 더 걸릴 수 있음 */
  domain?: DomainStatus
  log: string[]
  url?: string
  /** 실패 시 AI가 로그를 보고 정리한 원인과 수정안 */
  diagnosis?: {
    cause: string
    fix: string
    patch: Patch
  }
}

export interface DeployRecord {
  id: string
  app: string
  version: string
  tier: string
  provider: Provider
  target: string
  monthlyUsd: number
  status: 'success' | 'failed' | 'running'
  note?: string
  url?: string
  createdAt: string
}


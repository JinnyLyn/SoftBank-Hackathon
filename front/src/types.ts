export type Provider = 'aws' | 'gcp' | 'azure' | 'onprem'

export interface Connection {
  id: string
  provider: Provider
  name: string
  /** pending: 사용자가 벤더 콘솔에서 연결 작업을 마치길 기다리는 중 */
  status: 'connected' | 'pending' | 'error'
  /** 목록에 보여 줄 한 줄 요약 (계정, 리전, 호스트 등) */
  detail: string
  error?: string
  /** pending일 때 사용자가 열어야 할 벤더 콘솔 주소 (예: AWS CloudFormation 빠른 생성) */
  setupUrl?: string
  /** (온프레미스) pending일 때 서버에서 실행할 설치 명령. 일회용 토큰이 들어 있음 */
  installCommand?: string
  /** installCommand 만료 시각 (ISO) */
  expiresAt?: string
  checkedAt: string
  /** 폼에 입력한 원본 값. 비밀 값은 서버가 돌려주지 않음 */
  fields: Record<string, string>
}

export interface ConnectionInput {
  id?: string
  provider: Provider
  name: string
  fields: Record<string, string>
}

export type Source =
  | { kind: 'zip'; file: File }
  | { kind: 'github'; url: string; branch: string }

export type ExpectedUsers = '~100' | '~1,000' | '~10,000' | '10,000+'
export type TrafficPattern = 'steady' | 'peak' | 'unknown'

export interface ScaleInput {
  expectedUsers: ExpectedUsers
  pattern: TrafficPattern
  purpose: string
}

export interface Finding {
  level: 'info' | 'warn'
  title: string
  detail: string
}

export interface Analysis {
  projectId: string
  /** 코드에서 읽어낸 스택 정보 (프레임워크, 포트 등) */
  stack: { label: string; value: string }[]
  findings: Finding[]
  /** 스캐너가 찾은 판단 근거 */
  evidence: string[]
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
  recommended: Choice
  reason: string
  options: TargetOption[]
  assumptions: string[]
  /**
   * 백엔드가 미리 만들어 둔 배포 코드. 키는 `${connectionId}:${tier}`.
   * 최소한 추천 조합은 들어 있어야 코드 검토 화면이 바로 뜸
   */
  bundles?: Record<string, TerraformBundle>
}

export interface TerraformBundle {
  /** 온프레미스는 Terraform 대신 docker compose 사용 */
  tool: 'terraform' | 'compose'
  files: { name: string; content: string }[]
  plan: { add: number; change: number; destroy: number; text: string }
}

export interface DeployStatus {
  state: 'running' | 'success' | 'failed'
  log: string[]
  url?: string
  /** 실패 시 AI가 로그를 보고 정리한 원인과 수정안 */
  diagnosis?: {
    cause: string
    fix: string
    patch: { file: string; before: string[]; after: string[] }
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
  /** 승인한 사람 (감사 기록) */
  approvedBy: string
  createdAt: string
}

export interface User {
  id: string
  name: string
  email: string
  org: string
  role: 'admin' | 'member'
}

/** 이메일 도메인으로 찾은 회사 IdP */
export interface SsoDiscovery {
  org: string
  /** 화면 표시용 IdP 이름 (Okta, Entra ID 등) */
  idp: string
  protocol: 'oidc' | 'saml'
  /** 백엔드가 만든 IdP 로그인 주소. 여기로 이동하면 끝나고 / 로 돌아옴 */
  redirectUrl: string
}

export interface Session {
  user: User
  /** 상태를 바꾸는 요청에 X-CSRF-Token 헤더로 실어 보냄 */
  csrfToken: string
}

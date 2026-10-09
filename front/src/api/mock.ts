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
  TierKey,
  User,
} from '../types'
import { ApiError, emitUnauthorized } from './http'
import { buildFiles, buildPlan, CATALOG, findTier, logScript, publicUrl } from './catalog'
import { PROVIDERS } from '../providers'
import { now } from '../format'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// 소스 이름에 fail 이 들어가면 헬스체크 실패 시나리오로 진행 (데모용)
let failScenario = false
let deployStartedAt = 0
let deployChoice: Choice | null = null

export const sourceName = (s: Source) =>
  s.kind === 'zip'
    ? s.file.name.replace(/\.zip$/i, '')
    : s.url.replace(/\/+$/, '').replace(/\.git$/, '').split('/').pop() || 'app'

// ---------- 인증 (SSO) ----------
// 실제로는 백엔드가 IdP와 OIDC/SAML로 주고받고 HttpOnly 쿠키를 심음.
// mock은 데모용으로 탭 세션 저장소에 로그인 여부만 기억

const SESSION_KEY = 'pc-mock-session'
const PERSONAL_DOMAINS = ['gmail.com', 'naver.com', 'daum.net', 'hanmail.net', 'kakao.com', 'outlook.com', 'yahoo.com']
const IDPS: [string, SsoDiscovery['protocol']][] = [
  ['Microsoft Entra ID', 'oidc'],
  ['Okta', 'oidc'],
  ['Google Workspace', 'oidc'],
  ['Keycloak', 'saml'],
]

let currentUser: User | null = null

function readSession(): User | null {
  if (currentUser) return currentUser
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    currentUser = raw ? (JSON.parse(raw) as User) : null
  } catch {
    currentUser = null
  }
  return currentUser
}

function requireUser(): User {
  const u = readSession()
  if (!u) {
    emitUnauthorized()
    throw new ApiError(401, '로그인이 만료되었습니다.')
  }
  return u
}

function requireAdmin() {
  if (requireUser().role !== 'admin') throw new ApiError(403, '배포 대상은 관리자만 바꿀 수 있습니다.')
}

export async function session(): Promise<Session | null> {
  await wait(250)
  const user = readSession()
  return user ? { user, csrfToken: 'mock-' + user.id } : null
}

export async function discover(email: string): Promise<SsoDiscovery> {
  await wait(500)
  const domain = email.split('@')[1]?.toLowerCase() ?? ''
  if (PERSONAL_DOMAINS.includes(domain)) throw new ApiError(400, '개인 메일은 쓸 수 없습니다. 회사 이메일을 입력해 주세요.')
  const [idp, protocol] = IDPS[[...domain].reduce((n, c) => n + c.charCodeAt(0), 0) % IDPS.length]
  return {
    org: domain.split('.')[0].toUpperCase(),
    idp,
    protocol,
    redirectUrl: `/api/auth/sso/start?domain=${encodeURIComponent(domain)}`,
  }
}

/** mock 전용: IdP 왕복을 흉내 내고 로그인 처리. 이메일에 +member 가 있으면 일반 사용자 */
export async function mockCompleteSso(email: string, d: SsoDiscovery): Promise<Session> {
  await wait(1200)
  const local = email.split('@')[0]
  const user: User = {
    id: 'u_' + local.replace(/\W/g, ''),
    name: local.replace(/\+.*/, ''),
    email,
    org: d.org,
    role: local.includes('+member') ? 'member' : 'admin',
  }
  currentUser = user
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(user))
  } catch {
    // 저장소를 못 쓰면 새로고침 때 다시 로그인
  }
  return { user, csrfToken: 'mock-' + user.id }
}

export async function logout(): Promise<void> {
  await wait(150)
  currentUser = null
  try {
    sessionStorage.removeItem(SESSION_KEY)
  } catch {
    // 무시
  }
}

// ---------- 연결 ----------

let connections: Connection[] = [
  {
    id: 'c1',
    provider: 'aws',
    name: '개인 AWS',
    status: 'connected',
    detail: '계정 123456789012',
    checkedAt: '2026-10-07 13:40',
    fields: { budget: '30' },
  },
  {
    id: 'c2',
    provider: 'onprem',
    name: '동아리방 서버',
    status: 'connected',
    detail: 'deploy@192.168.0.24 · 4 vCPU / 8GB · Docker 27.1',
    checkedAt: '2026-10-07 13:42',
    fields: { host: '192.168.0.24', port: '22', user: 'deploy', path: '/srv/apps' },
  },
  {
    id: 'c3',
    provider: 'gcp',
    name: '학교 GCP 크레딧',
    status: 'error',
    detail: '프로젝트 paved-demo-4412',
    error: '서비스 계정에 Cloud Run 관리자 권한이 없습니다.',
    checkedAt: '2026-10-07 13:45',
    fields: { projectId: 'paved-demo-4412', serviceAccount: 'paved@paved-demo-4412.iam.gserviceaccount.com', budget: '50' },
  },
]

function describe(input: ConnectionInput): string {
  const f = input.fields
  switch (input.provider) {
    case 'aws':
      return '스택 생성 대기 중'
    case 'gcp':
      return `프로젝트 ${f.projectId}`
    case 'azure':
      return `구독 ${(f.subscriptionId ?? '').slice(0, 8)}…`
    case 'onprem':
      return '서버에서 설치 명령 실행 대기'
  }
}

// 온프레미스: 설치 명령을 낸 시각. mock은 6초 뒤 서버가 보고한 것으로 처리
const installIssuedAt = new Map<string, number>()
const INSTALL_TTL_MS = 10 * 60 * 1000

function issueInstall(id: string) {
  installIssuedAt.set(id, Date.now())
  const token = 'pc_' + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('')
  return {
    installCommand: `curl -fsSL https://pavedclouds.dev/install.sh | sudo sh -s -- --token ${token}`,
    expiresAt: new Date(Date.now() + INSTALL_TTL_MS).toISOString(),
  }
}

const AWS_STACK_URL =
  'https://console.aws.amazon.com/cloudformation/home#/stacks/quickcreate' +
  '?stackName=paved-clouds&templateURL=https://pavedclouds-public.s3.amazonaws.com/connect.yaml&param_ExternalId=pc-7f3a91'

export async function listConnections(): Promise<Connection[]> {
  await wait(150)
  return connections
}

export async function saveConnection(input: ConnectionInput): Promise<Connection> {
  requireAdmin()
  await wait(900)
  const prev = connections.find((c) => c.id === input.id)
  // 이미 연결된 걸 수정(이름 변경 등)할 때는 연결 상태 그대로
  if (prev?.status === 'connected') {
    const conn = { ...prev, name: input.name, fields: { ...prev.fields, ...input.fields } }
    connections = connections.map((c) => (c.id === conn.id ? conn : c))
    return conn
  }
  // AWS는 콘솔에서 스택 생성, 온프레미스는 서버에서 설치 명령 실행을 기다림
  const waits = input.provider === 'aws' || input.provider === 'onprem'
  const id = input.id ?? 'c' + Date.now()
  const conn: Connection = {
    id,
    provider: input.provider,
    name: input.name,
    status: waits ? 'pending' : 'connected',
    detail: describe(input),
    setupUrl: input.provider === 'aws' ? AWS_STACK_URL : undefined,
    ...(input.provider === 'onprem' ? issueInstall(id) : {}),
    checkedAt: now(),
    fields: input.fields,
  }
  connections = input.id ? connections.map((c) => (c.id === input.id ? conn : c)) : [...connections, conn]
  return conn
}

export async function checkConnection(id: string): Promise<Connection> {
  await wait(800)
  const c = connections.find((x) => x.id === id)!
  let next: Connection = { ...c, checkedAt: now() }
  if (c.status === 'pending' && c.provider === 'aws') {
    // mock: 스택을 만들었다고 보고 연결 완료 처리
    next = { ...next, status: 'connected', detail: '계정 ' + String(100000000000 + Math.floor(Math.random() * 9e11)), setupUrl: undefined }
  }
  if (c.status === 'pending' && c.provider === 'onprem' && Date.now() - (installIssuedAt.get(id) ?? Date.now()) > 6000) {
    // mock: 설치 스크립트가 서버 사양을 보고했다고 처리
    next = {
      ...next,
      status: 'connected',
      detail: 'deploy@192.168.0.31 · 4 vCPU / 8GB · Docker 27.3',
      installCommand: undefined,
      expiresAt: undefined,
      fields: { ...c.fields, host: '192.168.0.31', user: 'deploy', vcpu: '4', memoryGb: '8' },
    }
  }
  connections = connections.map((x) => (x.id === id ? next : x))
  return next
}

export async function deleteConnection(id: string): Promise<void> {
  requireAdmin()
  await wait(200)
  connections = connections.filter((c) => c.id !== id)
}

// ---------- 분석, 추천 ----------

export async function analyze(source: Source, _scale: ScaleInput): Promise<Analysis> {
  failScenario = /fail/i.test(source.kind === 'zip' ? source.file.name : source.url)
  await wait(source.kind === 'github' ? 2000 : 1400)
  return {
    projectId: 'p_' + Math.random().toString(36).slice(2, 8),
    stack: [
      { label: '프레임워크', value: 'FastAPI 0.111' },
      { label: '런타임', value: 'Python 3.12' },
      { label: '포트', value: '8000' },
      { label: 'DB', value: 'SQLite' },
      { label: '정적 파일', value: '없음' },
      { label: '환경 변수', value: 'SECRET_KEY 외 2개' },
    ],
    findings: [
      {
        level: 'warn',
        title: 'SQLite 파일에 데이터를 저장하고 있습니다',
        detail: '서버 한 대 구성이면 디스크에 그대로 두면 되지만, 컨테이너 구성에서는 재시작할 때 지워지므로 PostgreSQL로 옮깁니다.',
      },
      { level: 'info', title: 'Dockerfile이 없어서 새로 만듭니다', detail: 'python:3.12-slim 기반, uvicorn으로 8000번 포트 실행.' },
      { level: 'info', title: '헬스체크 경로 /health 를 찾았습니다', detail: '배포 후 확인과 로드밸런서 헬스체크에 씁니다.' },
    ],
    evidence: [
      'requirements.txt — fastapi, uvicorn, sqlalchemy',
      'app/db.py:4 — sqlite3.connect("app.db")',
      'app/main.py:12 — @app.get("/health")',
      '.env.example — SECRET_KEY, ADMIN_EMAIL, SMTP_HOST',
    ],
  }
}

const total = (resources: { monthlyUsd: number }[]) => resources.reduce((s, r) => s + r.monthlyUsd, 0)

export async function recommend(_projectId: string, scale: ScaleInput): Promise<Recommendation> {
  await wait(700)
  const usable = connections.filter((c) => c.status === 'connected')
  const options = usable.map((c) => ({
    connectionId: c.id,
    provider: c.provider,
    name: c.name,
    tiers: CATALOG[c.provider].map(({ resources, ...t }) => ({
      ...t,
      resources: resources.map(({ addr: _addr, ...r }) => r),
    })),
  }))

  const tier: TierKey = scale.expectedUsers === '~100' ? 'lean' : scale.expectedUsers === '~1,000' ? 'balanced' : 'roomy'
  // 큰 규모는 서버 한 대(온프레미스)에 몰지 않음. 나머지 중 가장 싼 곳
  const candidates = options.filter((o) => tier !== 'roomy' || o.provider !== 'onprem')
  const best = [...candidates].sort(
    (a, b) => total(findTier(a.provider, tier).resources) - total(findTier(b.provider, tier).resources),
  )[0]

  const where = best ? `${best.name}(${PROVIDERS[best.provider].label})` : ''
  const reason = !best
    ? '연결된 배포 대상이 없습니다.'
    : best.provider === 'onprem'
      ? `이미 연결된 ${where}에 올리면 추가 비용 없이 운영할 수 있습니다. 사용자가 늘면 클라우드로 옮기세요.`
      : `월 사용자 ${scale.expectedUsers}명이면 '${findTier(best.provider, tier).label}' 구성이 맞습니다. 연결된 대상 중 가장 싼 곳은 ${where}입니다.`

  const recommended = { connectionId: best?.connectionId ?? '', tier }
  // 추천 조합 코드는 추천과 같이 만들어 보냄 → 코드 검토가 바로 뜸
  const bundles: Recommendation['bundles'] = {}
  if (best) bundles[`${best.connectionId}:${tier}`] = buildBundle(recommended)

  return {
    recommended,
    reason,
    bundles,
    options,
    assumptions: [
      `월 사용자 ${scale.expectedUsers}명, ${scale.pattern === 'peak' ? '특정 시간에 몰림' : scale.pattern === 'steady' ? '고르게 들어옴' : '패턴 모름'}`,
      '클라우드는 서울 리전 온디맨드 가격, 데이터 전송 비용과 무료 크레딧은 제외',
      '온프레미스는 전기, 회선 비용을 넣지 않음',
    ],
  }
}

// ---------- 코드 생성, 배포 ----------

export async function generate(_projectId: string, choice: Choice): Promise<TerraformBundle> {
  await wait(1600)
  return buildBundle(choice)
}

function buildBundle(choice: Choice): TerraformBundle {
  const conn = connections.find((c) => c.id === choice.connectionId)!
  const tier = findTier(conn.provider, choice.tier)
  const n = tier.resources.length
  return {
    tool: conn.provider === 'onprem' ? 'compose' : 'terraform',
    files: buildFiles(conn.provider, tier),
    plan: { add: n, change: 0, destroy: 0, text: buildPlan(conn.provider, tier) },
  }
}

export async function approve(_projectId: string, choice: Choice): Promise<void> {
  requireUser()
  await wait(300)
  // 실패 후 다시 승인하면 AI 수정안이 반영된 것으로 보고 성공시킴
  if (deployStartedAt) failScenario = false
  deployStartedAt = Date.now()
  deployChoice = choice
}

export async function status(_projectId: string): Promise<DeployStatus> {
  await wait(120)
  const conn = connections.find((c) => c.id === deployChoice?.connectionId)!
  const tier = findTier(conn.provider, deployChoice!.tier)
  const script = logScript(conn.provider, tier, conn.fields.host)
  const end = script[script.length - 1][0] + 1200
  const t = Date.now() - deployStartedAt
  const log = script.filter(([at]) => at <= t).map(([, l]) => l)
  if (t < end) return { state: 'running', log }

  if (failScenario) {
    return {
      state: 'failed',
      log: [...log, 'curl: (7) Failed to connect: Connection refused', 'health check failed after 5 attempts'],
      diagnosis: {
        cause: '앱이 127.0.0.1에서만 요청을 받고 있어서 컨테이너 바깥에서 접속할 수 없습니다.',
        fix: 'Dockerfile의 실행 명령에 --host 0.0.0.0 을 넣고 같은 계획으로 다시 배포합니다. 인프라는 바뀌지 않습니다.',
        patch: {
          file: 'Dockerfile',
          before: ['CMD ["uvicorn", "app.main:app", "--port", "8000"]'],
          after: ['CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]'],
        },
      },
    }
  }
  return {
    state: 'success',
    log: [...log, '{"status":"ok"}', 'health check passed'],
    url: publicUrl(conn.provider, tier.key, conn.fields.host),
  }
}

export async function history(): Promise<DeployRecord[]> {
  await wait(200)
  return [
    { id: 'd6', app: 'club-attendance', version: 'v3', tier: '권장', provider: 'aws', target: '개인 AWS', monthlyUsd: 50.45, status: 'success', url: 'https://club-attendance-alb.ap-northeast-2.elb.amazonaws.com', approvedBy: 'jinny', createdAt: '2026-10-07 14:12' },
    { id: 'd5', app: 'club-attendance', version: 'v2', tier: '권장', provider: 'aws', target: '개인 AWS', monthlyUsd: 50.45, status: 'failed', note: '헬스체크 실패 → 포트 수정 후 v3', approvedBy: 'totoro', createdAt: '2026-10-07 13:58' },
    { id: 'd4', app: 'club-attendance', version: 'v1', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'success', approvedBy: 'jinny', createdAt: '2026-10-05 18:03' },
    { id: 'd3', app: 'todo-api', version: 'v2', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'success', url: 'http://192.168.0.24:8080', approvedBy: 'minsu', createdAt: '2026-10-04 11:30' },
    { id: 'd2', app: 'todo-api', version: 'v1', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'failed', note: 'requirements.txt 누락', approvedBy: 'minsu', createdAt: '2026-10-04 11:02' },
    { id: 'd1', app: 'portfolio', version: 'v1', tier: '권장', provider: 'gcp', target: '학교 GCP 크레딧', monthlyUsd: 17.62, status: 'success', url: 'https://portfolio-8xk2-du.a.run.app', approvedBy: 'jinny', createdAt: '2026-09-28 20:15' },
  ]
}

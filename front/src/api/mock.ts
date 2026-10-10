import type {
  Analysis,
  Choice,
  Connection,
  ConnectionInput,
  DnsRecord,
  DomainChoice,
  DomainPlan,
  DomainQuote,
  DomainStatus,
  DeployRecord,
  DeployStatus,
  Provider,
  Recommendation,
  ScaleInput,
  Source,
  TerraformBundle,
  TierKey,
} from '../types'
import { buildFiles, buildPlan, CATALOG, findTier, logScript, publicUrl } from './catalog'
import { isEnabled, PROVIDERS } from '../providers'
import { now } from '../format'
export { sourceName } from '../format'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// 소스 이름에 fail 이 들어가면 헬스체크 실패 시나리오로 진행 (데모용)
let failScenario = false
let deployStartedAt = 0
let deployChoice: Choice | null = null


// ---------- 연결 ----------

let connections: Connection[] = [
  {
    id: 'c1',
    provider: 'aws',
    name: '개인 AWS',
    status: 'connected',
    detail: '계정 123456789012',
    checkedAt: '2026-10-07 13:40',
    fields: {},
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
]

const describe = (input: ConnectionInput) =>
  input.provider === 'aws' ? '스택 생성 대기 중' : '서버에서 설치 명령 실행 대기'

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
  return connections.filter((c) => isEnabled(c.provider))
}

export async function saveConnection(input: ConnectionInput): Promise<Connection> {
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
  const usable = connections.filter((c) => c.status === 'connected' && isEnabled(c.provider))
  const options = usable.map((c) => ({
    connectionId: c.id,
    provider: c.provider,
    name: c.name,
    tiers: CATALOG[c.provider].map(({ resources, ...t }) => ({
      ...t,
      resources: resources.map(({ addr: _addr, ...r }) => r),
    })),
  }))

  const budget = scale.monthlyBudgetUsd
  const wanted: TierKey = scale.expectedUsers === '~100' ? 'lean' : scale.expectedUsers === '~1,000' ? 'balanced' : 'roomy'
  const cost = (p: Provider, t: TierKey) => total(findTier(p, t).resources)

  // 규모에 맞는 크기부터 예산 안에서 가장 싼 곳을 찾고, 없으면 한 단계씩 작은 크기로 다시 추천
  // 큰 규모는 서버 한 대(온프레미스)에 몰지 않음
  const order: TierKey[] = ['lean', 'balanced', 'roomy']
  let best: (typeof options)[number] | undefined
  let tier: TierKey = wanted
  for (const t of order.slice(0, order.indexOf(wanted) + 1).reverse()) {
    best = options
      .filter((o) => (t !== 'roomy' || o.provider !== 'onprem') && cost(o.provider, t) <= budget)
      .sort((a, b) => cost(a.provider, t) - cost(b.provider, t))[0]
    tier = t
    if (best) break
  }

  const label = (t: TierKey) => findTier('aws', t).label
  const where = best ? `${best.name}(${PROVIDERS[best.provider].label})` : ''
  let reason: string
  if (options.length === 0) reason = '연결된 배포 대상이 없습니다.'
  else if (!best) {
    const cheapest = Math.min(...options.map((o) => cost(o.provider, 'lean')))
    reason = `월 예산 $${budget} 안에 맞는 구성이 없습니다. 가장 싼 구성도 월 $${cheapest.toFixed(2)}입니다. 예산을 늘려 주세요.`
  } else {
    const downgraded = tier !== wanted ? ` 규모로는 '${label(wanted)}'이 맞지만 예산 $${budget}을 넘어서 '${label(tier)}'으로 낮췄습니다.` : ''
    reason =
      best.provider === 'onprem'
        ? `이미 연결된 ${where}에 올리면 추가 비용 없이 운영할 수 있습니다.${downgraded}`
        : `예산 $${budget} 안에서 '${label(tier)}' 구성이 가장 싼 곳은 ${where}입니다.${downgraded}`
  }

  const recommended = best ? { connectionId: best.connectionId, tier } : null
  // 추천 조합 코드는 추천과 같이 만들어 보냄 → 코드 검토가 바로 뜸
  const bundles: Recommendation['bundles'] = {}
  if (recommended) bundles[`${recommended.connectionId}:${tier}`] = buildBundle(recommended)

  return {
    recommended,
    reason,
    bundles,
    options,
    assumptions: [
      `월 사용자 ${scale.expectedUsers}명, 월 예산 $${budget}, ${scale.pattern === 'peak' ? '특정 시간에 몰림' : scale.pattern === 'steady' ? '고르게 들어옴' : '패턴 모름'}`,
      '클라우드는 상파울루(sa-east-1) 리전 온디맨드 가격, 데이터 전송 비용과 무료 크레딧은 제외',
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
  await wait(300)
  deployStartedAt = Date.now()
  deployChoice = choice
}

export async function applyFix(_projectId: string, choice: Choice): Promise<TerraformBundle> {
  await wait(1500)
  // mock: 수정안(Dockerfile --host 0.0.0.0)이 반영돼 다음 배포는 성공
  failScenario = false
  return { ...buildBundle(choice), patches: [HOST_PATCH] }
}

const HOST_PATCH = {
  file: 'Dockerfile',
  before: ['CMD ["uvicorn", "app.main:app", "--port", "8000"]'],
  after: ['CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]'],
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
        patch: HOST_PATCH,
      },
    }
  }
  const appUrl = publicUrl(conn.provider, tier.key, conn.fields.host)
  return {
    state: 'success',
    log: [...log, '{"status":"ok"}', 'health check passed'],
    url: appUrl,
    domain: domainProgress(t - end, appUrl),
  }
}

// ---------- 도메인 (mock) ----------
// 실제 구매·DNS·인증서는 백엔드·인프라 작업이 필요함. 여기서는 화면 흐름만 흉내

// Route 53 등록 가격을 흉내 낸 값 (USD/년). 실제 가격은 서버가 가격표로 계산해야 함
const TLD_PRICE: Record<string, number> = { com: 15, net: 17, org: 15, dev: 17, app: 20, io: 71, xyz: 13 }
const TAKEN = /(google|naver|amazon|kakao|test)\./
let domainChoice: DomainChoice = { mode: 'later', name: '' }

export async function checkDomain(name: string): Promise<DomainQuote> {
  await wait(500)
  const tld = name.split('.').pop() ?? ''
  const price = TLD_PRICE[tld] ?? null
  if (price === null)
    return { name, available: false, priceUsdPerYear: null, reason: `.${tld} 도메인은 구매를 지원하지 않습니다. .com .net .org .dev .app .io .xyz 중에서 골라 주세요.` }
  if (TAKEN.test(name)) {
    const base = name.slice(0, -(tld.length + 1))
    return {
      name,
      available: false,
      priceUsdPerYear: price,
      reason: '이미 등록된 도메인입니다.',
      suggestions: [`get${base}.${tld}`, `${base}-app.${tld}`, `${base}.dev`],
    }
  }
  return { name, available: true, priceUsdPerYear: price }
}

const appRecords = (name: string): DnsRecord[] => [
  { type: 'CNAME', name, value: '배포 뒤 확정 (예: paved-alb-1203.sa-east-1.elb.amazonaws.com)', purpose: '도메인을 앱 주소로 연결' },
  { type: 'CNAME', name: `_3f9a1c.${name}`, value: '_8d2e0b.acm-validations.aws', purpose: 'HTTPS 인증서 발급 확인용' },
]

export async function saveDomain(_projectId: string, choice: DomainChoice): Promise<DomainPlan> {
  await wait(300)
  domainChoice = choice
  if (choice.mode === 'later')
    return { mode: 'later', name: null, oneTimeUsd: 0, monthlyUsd: 0, records: [], note: 'AWS 기본 주소로 접속합니다. 도메인은 나중에 연결할 수 있습니다.' }
  if (choice.mode === 'own')
    return { mode: 'own', name: choice.name, oneTimeUsd: 0, monthlyUsd: 0, records: appRecords(choice.name) }
  const price = TLD_PRICE[choice.name.split('.').pop() ?? ''] ?? 0
  return {
    mode: 'buy',
    name: choice.name,
    oneTimeUsd: price,
    // Route 53 호스팅 영역 1개
    monthlyUsd: 0.5,
    records: [],
    note: '구매한 도메인의 DNS와 인증서는 자동으로 설정합니다.',
  }
}

export async function confirmDomainPurchase(_projectId: string, _plan: DomainPlan): Promise<void> {
  await wait(200)
}

/** 앱 배포가 끝난 뒤 지난 시간(ms)에 따라 도메인 단계를 흉내 */
function domainProgress(since: number, appUrl: string): DomainStatus {
  const { mode, name } = domainChoice
  if (mode === 'later') return { state: 'skipped', name: null, message: '도메인 없이 AWS 기본 주소로 접속합니다.', url: appUrl }
  if (mode === 'buy' && since < 3000) return { state: 'registering', name, message: '도메인을 등록하고 있습니다. 보통 몇 분 걸립니다.' }
  if (mode === 'own' && since < 4000)
    return { state: 'waiting_dns', name, message: '도메인 업체에 아래 레코드를 추가해 주세요. 추가하면 자동으로 확인합니다.', records: appRecords(name) }
  if (since < 7000) return { state: 'issuing_cert', name, message: 'HTTPS 인증서를 발급하고 있습니다.' }
  return { state: 'active', name, url: `https://${name}` }
}

export async function history(): Promise<DeployRecord[]> {
  await wait(200)
  return HISTORY.filter((r) => isEnabled(r.provider))
}

const HISTORY: DeployRecord[] = [
    { id: 'd6', app: 'club-attendance', version: 'v3', tier: '권장', provider: 'aws', target: '개인 AWS', monthlyUsd: 50.45, status: 'success', url: 'https://club-attendance-alb.sa-east-1.elb.amazonaws.com', createdAt: '2026-10-07 14:12' },
    { id: 'd5', app: 'club-attendance', version: 'v2', tier: '권장', provider: 'aws', target: '개인 AWS', monthlyUsd: 50.45, status: 'failed', note: '헬스체크 실패 → 포트 수정 후 v3', createdAt: '2026-10-07 13:58' },
    { id: 'd4', app: 'club-attendance', version: 'v1', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'success', createdAt: '2026-10-05 18:03' },
    { id: 'd3', app: 'todo-api', version: 'v2', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'success', url: 'http://192.168.0.24:8080', createdAt: '2026-10-04 11:30' },
    { id: 'd2', app: 'todo-api', version: 'v1', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'failed', note: 'requirements.txt 누락', createdAt: '2026-10-04 11:02' },
]

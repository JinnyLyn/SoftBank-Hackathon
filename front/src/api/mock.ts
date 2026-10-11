import type {
  Analysis,
  Choice,
  DnsRecord,
  DomainChoice,
  DomainPlan,
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
import { t as tr } from '../i18n/core'
export { sourceName } from '../format'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// 소스 이름에 fail 이 들어가면 헬스체크 실패 시나리오로 진행 (데모용)
let failScenario = false
let deployStartedAt = 0
let deployChoice: Choice | null = null


// ---------- 배포 대상 ----------
// 관리형 AWS(운영자가 준비한 계정)와, 온프레미스를 켰을 때의 예시 서버. 연결 관리 화면은 없앰(사용자 계정 연결 없이 배포)
interface MockTarget {
  id: string
  provider: Provider
  name: string
  host?: string
}
const TARGETS: MockTarget[] = [
  { id: 'managed-aws', provider: 'aws', name: '관리형 AWS' },
  { id: 'onprem-demo', provider: 'onprem', name: '동아리방 서버', host: '192.168.0.24' },
]
const findTarget = (id?: string) => TARGETS.find((t) => t.id === id) ?? TARGETS[0]

/** mock은 모든 안의 코드가 이미 있어 바로 고를 수 있음. 실제 백엔드와 모양만 맞춤 */
export async function requestTier(_projectId: string, _tier: TierKey): Promise<void> {
  await wait(200)
}

// ---------- 분석, 추천 ----------

export async function analyze(source: Source, _scale: ScaleInput, onUploaded?: () => void): Promise<Analysis> {
  failScenario = /fail/i.test(source.kind === 'zip' ? source.file.name : source.url)
  await wait(source.kind === 'github' ? 1200 : 600)
  onUploaded?.()
  await wait(2500)
  return {
    projectId: 'p_' + Math.random().toString(36).slice(2, 8),
    stack: [
      { label: tr('프레임워크'), value: 'FastAPI 0.111' },
      { label: tr('런타임'), value: 'Python 3.12' },
      { label: tr('포트'), value: '8000' },
      { label: 'DB', value: 'SQLite' },
      { label: tr('정적 파일'), value: tr('없음') },
      { label: tr('환경 변수'), value: tr('SECRET_KEY 외 2개') },
    ],
    findings: [
      {
        level: 'warn',
        title: tr('SQLite 파일에 데이터를 저장하고 있습니다'),
        detail: tr('서버 한 대 구성이면 디스크에 그대로 두면 되지만, 컨테이너 구성에서는 재시작할 때 지워지므로 PostgreSQL로 옮깁니다.'),
      },
      { level: 'info', title: tr('Dockerfile이 없어서 새로 만듭니다'), detail: tr('python:3.12-slim 기반, uvicorn으로 8000번 포트 실행.') },
      { level: 'info', title: tr('헬스체크 경로 /health 를 찾았습니다'), detail: tr('배포 후 확인과 로드밸런서 헬스체크에 씁니다.') },
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
  const usable = TARGETS.filter((t) => isEnabled(t.provider))
  const options = usable.map((c) => ({
    connectionId: c.id,
    provider: c.provider,
    name: tr(c.name),
    tiers: CATALOG[c.provider].map(({ resources, ...t }) => ({
      ...t,
      label: tr(t.label),
      fit: tr(t.fit),
      headline: tr(t.headline),
      tradeoff: tr(t.tradeoff),
      usageNote: t.usageNote && tr(t.usageNote),
      resources: resources.map(({ addr: _addr, ...r }) => ({ ...r, service: tr(r.service), spec: tr(r.spec), why: tr(r.why) })),
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

  const label = (t: TierKey) => tr(findTier('aws', t).label)
  const where = best ? `${best.name}(${tr(PROVIDERS[best.provider].label)})` : ''
  let reason: string
  if (options.length === 0) reason = tr('켜 둔 배포 대상이 없습니다.')
  else if (!best) {
    const cheapest = Math.min(...options.map((o) => cost(o.provider, 'lean')))
    reason = tr('월 예산 ${budget} 안에 맞는 구성이 없습니다. 가장 싼 구성도 월 ${cheapest}입니다. 예산을 늘려 주세요.', { budget, cheapest: cheapest.toFixed(2) })
  } else {
    const downgraded = tier !== wanted ? ' ' + tr("규모로는 '{wanted}'이 맞지만 예산 ${budget}을 넘어서 '{tier}'으로 낮췄습니다.", { wanted: label(wanted), budget, tier: label(tier) }) : ''
    reason =
      best.provider === 'onprem'
        ? tr('이미 연결된 {where}에 올리면 추가 비용 없이 운영할 수 있습니다.', { where }) + downgraded
        : tr("예산 ${budget} 안에서 '{tier}' 구성이 가장 싼 곳은 {where}입니다.", { budget, tier: label(tier), where }) + downgraded
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
      tr('월 사용자 {users}명, 월 예산 ${budget}, {pattern}', {
        users: scale.expectedUsers,
        budget,
        pattern: tr(scale.pattern === 'peak' ? '특정 시간에 몰림' : scale.pattern === 'steady' ? '고르게 들어옴' : '패턴 모름'),
      }),
      tr('클라우드는 상파울루(sa-east-1) 리전 온디맨드 가격, 데이터 전송 비용과 무료 크레딧은 제외'),
      tr('온프레미스는 전기, 회선 비용을 넣지 않음'),
    ],
  }
}

// ---------- 코드 생성, 배포 ----------

export async function generate(_projectId: string, choice: Choice): Promise<TerraformBundle> {
  await wait(1600)
  return buildBundle(choice)
}

function buildBundle(choice: Choice): TerraformBundle {
  const conn = findTarget(choice.connectionId)
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
  const conn = findTarget(deployChoice?.connectionId)
  const tier = findTier(conn.provider, deployChoice!.tier)
  const script = logScript(conn.provider, tier, conn.host)
  const end = script[script.length - 1][0] + 1200
  const t = Date.now() - deployStartedAt
  const log = script.filter(([at]) => at <= t).map(([, l]) => l)
  if (t < end) return { state: 'running', log }

  if (failScenario) {
    return {
      state: 'failed',
      log: [...log, 'curl: (7) Failed to connect: Connection refused', 'health check failed after 5 attempts'],
      diagnosis: {
        cause: tr('앱이 127.0.0.1에서만 요청을 받고 있어서 컨테이너 바깥에서 접속할 수 없습니다.'),
        fix: tr('Dockerfile의 실행 명령에 --host 0.0.0.0 을 넣고 같은 계획으로 다시 배포합니다. 인프라는 바뀌지 않습니다.'),
        patch: HOST_PATCH,
      },
    }
  }
  const appUrl = publicUrl(conn.provider, tier.key, conn.host)
  return {
    state: 'success',
    log: [...log, '{"status":"ok"}', 'health check passed'],
    url: appUrl,
    domain: domainProgress(t - end),
  }
}

// ---------- 도메인 (mock) ----------
// 실제 주소 발급·DNS·인증서는 백엔드·인프라 작업이 필요함. 여기서는 화면 흐름만 흉내.
// 자동 주소의 도메인은 예시용 예약 도메인(.example)

const WORDS = ['quiet', 'bright', 'swift', 'calm', 'lucky', 'river', 'otter', 'maple', 'cloud', 'pebble']
let domainChoice: DomainChoice = { mode: 'auto', name: '' }
let autoName = ''

const appRecords = (name: string): DnsRecord[] => [
  { type: 'CNAME', name, value: tr('배포 뒤 확정 (예: paved-alb-1203.sa-east-1.elb.amazonaws.com)'), purpose: tr('도메인을 앱 주소로 연결') },
  { type: 'CNAME', name: `_3f9a1c.${name}`, value: '_8d2e0b.acm-validations.aws', purpose: tr('HTTPS 인증서 발급 확인용') },
]

export async function saveDomain(_projectId: string, choice: DomainChoice): Promise<DomainPlan> {
  await wait(300)
  domainChoice = choice
  if (choice.mode === 'own') return { mode: 'own', name: choice.name, monthlyUsd: 0, records: appRecords(choice.name) }
  const pick = () => WORDS[Math.floor(Math.random() * WORDS.length)]
  autoName = `${pick()}-${pick()}-${Math.random().toString(16).slice(2, 6)}.apps.paved.example`
  return { mode: 'auto', name: autoName, monthlyUsd: 0, records: [], note: tr('플랫폼 도메인 아래의 자동 주소라 추가 비용이 없습니다. 내 도메인은 아니며 HTTPS 인증서는 플랫폼이 관리합니다.') }
}

/** 앱 배포가 끝난 뒤 지난 시간(ms)에 따라 도메인 단계를 흉내 */
function domainProgress(since: number): DomainStatus {
  const name = domainChoice.mode === 'auto' ? autoName : domainChoice.name
  if (since < 3000)
    return domainChoice.mode === 'auto'
      ? { state: 'waiting_dns', name, message: tr('주소를 앱에 연결하고 있습니다.') }
      : { state: 'waiting_dns', name, message: tr('도메인 업체에 아래 레코드를 추가해 주세요. 추가하면 자동으로 확인합니다.'), records: appRecords(name) }
  if (since < 5000) return { state: 'issuing_cert', name, message: tr('HTTPS를 준비하고 있습니다.') }
  return { state: 'active', name, url: `https://${name}` }
}

export async function history(): Promise<DeployRecord[]> {
  await wait(200)
  return HISTORY.filter((r) => isEnabled(r.provider)).map((r) => ({
    ...r,
    tier: tr(r.tier),
    target: tr(r.target),
    note: r.note && tr(r.note),
  }))
}

const HISTORY: DeployRecord[] = [
    { id: 'd6', app: 'club-attendance', version: 'v3', tier: '권장', provider: 'aws', target: '개인 AWS', monthlyUsd: 50.45, status: 'success', url: 'https://club-attendance-alb.sa-east-1.elb.amazonaws.com', createdAt: '2026-10-07 14:12' },
    { id: 'd5', app: 'club-attendance', version: 'v2', tier: '권장', provider: 'aws', target: '개인 AWS', monthlyUsd: 50.45, status: 'failed', note: '헬스체크 실패 → 포트 수정 후 v3', createdAt: '2026-10-07 13:58' },
    { id: 'd4', app: 'club-attendance', version: 'v1', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'success', createdAt: '2026-10-05 18:03' },
    { id: 'd3', app: 'todo-api', version: 'v2', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'success', url: 'http://192.168.0.24:8080', createdAt: '2026-10-04 11:30' },
    { id: 'd2', app: 'todo-api', version: 'v1', tier: '작게 시작', provider: 'onprem', target: '동아리방 서버', monthlyUsd: 0, status: 'failed', note: 'requirements.txt 누락', createdAt: '2026-10-04 11:02' },
]

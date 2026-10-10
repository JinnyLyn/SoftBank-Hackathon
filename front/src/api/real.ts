// 실제 백엔드(FastAPI, back/API.md) 연동.
// 백엔드 응답을 화면 타입(types.ts)으로 바꾸는 일은 여기서만 함
import type {
  Analysis,
  Choice,
  Connection,
  ConnectionInput,
  DeployRecord,
  DeployStatus,
  DnsRecord,
  DomainChoice,
  DomainPlan,
  DomainStatus,
  Finding,
  Recommendation,
  ScaleInput,
  Source,
  TerraformBundle,
  Tier,
  TierKey,
} from '../types'
import { ApiError, req, reqOrNull, send } from './http'
import { sourceName, TIER_META, usd } from '../format'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 결과가 생길 때까지 주기적으로 확인. 시간이 지나면 이유를 담아 실패 */
async function poll<T>(
  fn: () => Promise<T | null>,
  opts: { intervalMs: number; timeoutMs: number; what: string; hint?: string },
) {
  const until = Date.now() + opts.timeoutMs
  for (;;) {
    const v = await fn()
    if (v) return v
    if (Date.now() > until)
      throw new ApiError(
        408,
        `${Math.round(opts.timeoutMs / 60000)}분 동안 기다렸지만 ${opts.what}이(가) 오지 않았습니다. ${opts.hint ?? '잠시 뒤 다시 시도해 주세요.'}`,
      )
    await wait(opts.intervalMs)
  }
}

// ---------- 프로젝트 ----------

interface ProjectOut {
  id: string
  name: string
  source_filename: string
  source_sha256: string
  source_size_bytes: number
  source_type: 'zip' | 'github'
  source_url: string | null
  source_ref: string | null
  created_at: string
}

/** ZIP은 원본 바이트를 그대로, GitHub는 주소와 ref를 JSON으로 */
/**
 * 사용 규모·예산. 인프라 worker가 구성 단계와 예산 검사에 쓰는 사용자 입력이라 그대로 보냄 (AI가 바꾸지 않음).
 * 백엔드가 프로젝트에 저장하고, AI runner가 분석 결과 scale 로 복사해 worker에 넘김
 */
function scaleParams(scale: ScaleInput): Record<string, string> {
  const p: Record<string, string> = { expected_users: scale.expectedUsers, traffic_pattern: scale.pattern }
  if (Number.isFinite(scale.monthlyBudgetUsd)) p.monthly_budget_usd = String(scale.monthlyBudgetUsd)
  if (scale.purpose.trim()) p.purpose = scale.purpose.trim().slice(0, 500)
  return p
}

function createProject(source: Source, scale: ScaleInput): Promise<ProjectOut> {
  const name = sourceName(source)
  if (source.kind === 'zip') {
    // 본문이 ZIP 원본이라 사용 규모는 쿼리로
    const q = new URLSearchParams({ name, filename: source.file.name, ...scaleParams(scale) })
    return req<ProjectOut>(`/projects?${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: source.file,
      // 최대 200MB 업로드라 기본 제한(20초)보다 길게
      timeoutMs: 10 * 60 * 1000,
    })
  }
  const { monthly_budget_usd, ...rest } = scaleParams(scale)
  return req<ProjectOut>(
    '/projects/github',
    send('POST', {
      name,
      repository_url: source.url,
      ref: source.branch || undefined,
      ...rest,
      monthly_budget_usd: monthly_budget_usd === undefined ? undefined : Number(monthly_budget_usd),
    }),
  )
}

// ---------- 분석 ----------

interface AnalysisOut {
  id: string
  project_id: string
  schema_version: string
  source_sha256: string
  result: Record<string, unknown>
  created_at: string
}

// 분석 결과(result)의 필드는 LLM 담당과 합의 중이라 백엔드가 고정하지 않음.
// 화면 형태(stack/findings/evidence)를 우선 읽고, 없으면 최상위 값들을 스택 표로 보여 줌
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))

function toAnalysis(out: AnalysisOut): Analysis {
  const r = out.result

  const stack = Array.isArray(r.stack)
    ? r.stack.filter(isObj).map((s) => ({ label: str(s.label), value: str(s.value) }))
    : Object.entries(r)
        .filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v))
        .map(([k, v]) => ({ label: k, value: str(v) }))

  const findings: Finding[] = (Array.isArray(r.findings) ? r.findings : []).filter(isObj).map((f) => ({
    level: f.level === 'warn' || f.level === 'warning' ? 'warn' : 'info',
    title: str(f.title ?? f.message),
    detail: str(f.detail ?? f.description),
  }))

  const evidence = (Array.isArray(r.evidence) ? r.evidence : []).map((e) =>
    isObj(e) ? [str(e.file) + (e.line ? `:${e.line}` : ''), str(e.text ?? e.reason)].filter(Boolean).join(' — ') : str(e),
  )

  return { projectId: out.project_id, stack, findings, evidence }
}

/**
 * 소스를 등록하고, 분석 모듈이 결과를 기록할 때까지 기다림.
 * 백엔드 API는 LLM 분석을 직접 돌리지 않음 → 분석 담당 모듈이 POST /analyses 로 결과를 남겨야 끝남
 * 사용 규모·예산(scale)은 백엔드 API에 받는 곳이 없어 화면에서 추천을 거를 때만 씀
 */
/** 프로젝트 등록 → 분석 결과 대기. 등록(업로드)이 끝나면 onUploaded 로 알림 (화면 진행 표시용) */
export async function analyze(source: Source, scale: ScaleInput, onUploaded?: () => void): Promise<Analysis> {
  const project = await createProject(source, scale)
  onUploaded?.()
  const out = await poll(() => reqOrNull<AnalysisOut>(`/projects/${project.id}/analyses/latest`), {
    intervalMs: 2000,
    timeoutMs: 5 * 60 * 1000,
    what: '분석 결과',
    hint: '분석 프로그램(ai/runner.py)이 실행 중인지 확인해 주세요.',
  })
  return toAnalysis(out)
}

// ---------- 배포 계획 → 추천 비교표, 코드 검토 ----------

interface PlanOut {
  id: string
  project_id: string
  analysis_id: string | null
  target: string
  module_id: string
  variables: Record<string, unknown>
  summary: string
  cost_estimate: { amount: string; currency: string; period: 'month'; pricing_as_of: string } | null
  terraform_plan_sha256: string | null
  terraform_plan_ready: boolean
  fingerprint: string
  status: 'awaiting_approval' | 'approved' | 'consumed' | 'superseded'
  created_at: string
  approved_at: string | null
}

const TIER_KEYS: TierKey[] = ['lean', 'balanced', 'roomy']
const keyOf = (c: Choice) => `${c.connectionId}:${c.tier}`
const planCost = (p: PlanOut) => Number(p.cost_estimate?.amount ?? 0)

// 화면에서 고른 칸(대상:크기) → 백엔드 계획. 승인할 때 plan id와 fingerprint를 여기서 찾음
let plansByKey = new Map<string, PlanOut>()

/** 계획 변수 중 화면 문구용 키를 빼고 짧게 요약 */
const META_KEYS = ['tier', 'recommended', 'headline', 'tradeoff', 'reason', 'resources', 'connection_name']
function variableSummary(v: Record<string, unknown>) {
  const text = Object.entries(v)
    .filter(([k, x]) => !META_KEYS.includes(k) && ['string', 'number', 'boolean'].includes(typeof x))
    .map(([k, x]) => `${k}=${x}`)
    .join(', ')
  return text.length > 120 ? text.slice(0, 117) + '…' : text
}

function toTier(p: PlanOut, key: TierKey): Tier {
  const v = p.variables
  // 계획 변수에 리소스 목록이 있으면 그대로, 없으면 계획 전체를 한 줄로 (금액은 백엔드 비용 추정)
  const resources = Array.isArray(v.resources)
    ? v.resources.filter(isObj).map((r) => ({
        service: str(r.service),
        spec: str(r.spec),
        monthlyUsd: Number(r.monthlyUsd ?? r.monthly_usd ?? 0),
        why: str(r.why),
      }))
    : [{ service: p.module_id, spec: variableSummary(v), monthlyUsd: planCost(p), why: p.summary }]
  return {
    key,
    ...TIER_META[key],
    headline: str(v.headline) || p.module_id,
    tradeoff: str(v.tradeoff),
    resources,
    // 화면의 모든 금액·예산 판단은 서버 추정 총액 하나로 (resources 합계와 달라도)
    totalUsd: planCost(p),
  }
}

function toBundle(p: PlanOut): TerraformBundle {
  return {
    tool: 'terraform',
    // AGENTS.md: 제품 LLM은 Terraform 변수값까지만 바꿈 → 검토 대상은 변수값과 계획 설명
    files: [{ name: 'variables.auto.tfvars.json', content: JSON.stringify(p.variables, null, 2) }],
    plan: {
      add: null,
      change: null,
      destroy: null,
      text: [
        `# module: ${p.module_id}`,
        `# terraform plan sha256: ${p.terraform_plan_sha256 ?? '(아직 없음)'}`,
        '',
        p.summary,
      ].join('\n'),
    },
    planInfo: {
      planId: p.id,
      moduleId: p.module_id,
      summary: p.summary,
      fingerprint: p.fingerprint,
      ready: p.terraform_plan_ready,
      pricingAsOf: p.cost_estimate?.pricing_as_of,
    },
  }
}

/**
 * 계획 모듈이 승인 대기 계획을 만들어 둘 때까지 기다린 뒤 비교표로 바꿈.
 * 크기는 계획 변수의 tier를 쓰고, 없으면 싼 순서대로 작게 시작 / 권장 / 여유 있게
 * 월 예산을 넘는 계획은 추천하지 않음 (화면에서도 고를 수 없음)
 */
export async function recommend(projectId: string, scale: ScaleInput): Promise<Recommendation> {
  const [plans, conns] = await Promise.all([
    poll(
      async () => {
        const list = await req<PlanOut[]>(`/projects/${projectId}/plans`)
        const waiting = list.filter((p) => p.status === 'awaiting_approval' && p.target === 'aws')
        return waiting.length ? waiting : null
      },
      {
        intervalMs: 2000,
        timeoutMs: 5 * 60 * 1000,
        what: '배포 계획',
        // 계획은 AI가 아니라 인프라 worker가 만듦. 분석은 이미 끝난 상태
        hint: '배포 계획은 인프라 worker(infra/worker)가 만듭니다. worker가 실행 중인지 확인한 뒤 다시 기다려 주세요.',
      },
    ),
    req<Connection[]>('/connections'),
  ])

  const aws = conns.find((c) => c.provider === 'aws' && c.status === 'connected')
  const connectionId = aws?.id ?? 'aws'

  // 크기별로 가장 최근 계획 하나씩
  const byTier = new Map<TierKey, PlanOut>()
  const sorted = [...plans].sort((a, b) => planCost(a) - planCost(b))
  sorted.forEach((p, i) => {
    const declared = TIER_KEYS.find((k) => k === p.variables.tier)
    const key = declared ?? TIER_KEYS[Math.min(i, TIER_KEYS.length - 1)]
    const prev = byTier.get(key)
    if (!prev || prev.created_at < p.created_at) byTier.set(key, p)
  })

  plansByKey = new Map()
  const bundles: Record<string, TerraformBundle> = {}
  const tiers: Tier[] = []
  for (const key of TIER_KEYS) {
    const p = byTier.get(key)
    if (!p) continue
    const k = keyOf({ connectionId, tier: key })
    plansByKey.set(k, p)
    bundles[k] = toBundle(p)
    tiers.push(toTier(p, key))
  }

  const budget = scale.monthlyBudgetUsd
  const within = [...byTier.entries()].filter(([, p]) => planCost(p) <= budget)
  const pick =
    within.find(([, p]) => p.variables.recommended === true) ?? within.sort((a, b) => planCost(a[1]) - planCost(b[1]))[0]

  const pricing = plans.find((p) => p.cost_estimate)?.cost_estimate?.pricing_as_of
  return {
    recommended: pick ? { connectionId, tier: pick[0] } : null,
    reason: pick
      ? str(pick[1].variables.reason) || pick[1].summary
      : `월 예산 ${usd(budget)} 안에 맞는 배포 계획이 없습니다. 가장 싼 계획도 월 ${usd(planCost(sorted[0]))}입니다. 예산을 늘려 주세요.`,
    // 연결이 없으면 worker가 쓰는 운영자 AWS (관리형 배포)
    options: [{ connectionId, provider: 'aws', name: aws?.name ?? '관리형 AWS', tiers }],
    assumptions: [
      `월 사용자 ${scale.expectedUsers}명, 월 예산 ${usd(budget)}`,
      pricing ? `가격 기준: ${pricing}` : '가격 기준일 정보 없음',
      '공유 기반(foundation) 비용 포함 여부는 계획 설명을 확인',
    ],
    bundles,
  }
}

/**
 * 계획은 백엔드가 이미 만들어 둠 → LLM을 다시 돌리지 않고 같은 계획을 다시 읽음.
 * plan 파일 준비 여부(terraform_plan_ready)가 바뀌었는지 확인할 때도 씀.
 * 다시 읽은 계획이 이후 승인 기준이 됨 (화면은 fingerprint가 바뀌면 확인 체크를 풀어 다시 검토하게 함)
 */
export async function generate(_projectId: string, choice: Choice): Promise<TerraformBundle> {
  const k = keyOf(choice)
  const p = plansByKey.get(k)
  if (!p) throw new ApiError(404, '이 구성의 배포 계획이 없습니다. 분석부터 다시 시작해 주세요.')
  const latest = await req<PlanOut>(`/plans/${p.id}`)
  plansByKey.set(k, latest)
  return toBundle(latest)
}

/** 이 계획으로 이미 등록된 배포가 있는지 */
async function hasDeployment(projectId: string, planId: string) {
  const q = new URLSearchParams({ project_id: projectId, limit: '100' })
  const rows = await req<{ plan_id: string }[]>(`/deployments?${q}`)
  return rows.some((r) => r.plan_id === planId)
}

/**
 * 승인 → 배포 대기열 등록. 중간에 실패해도 다시 누르면 서버 상태를 보고 실패한 단계부터 이어 감
 * - 이미 등록된 배포가 있으면 다시 등록하지 않고 끝냄 → 화면은 상태 조회로 넘어감 (중복 배포 방지)
 * - 이미 승인된 계획이면 승인을 건너뜀
 * - 검토한 뒤 계획 내용(fingerprint)이 바뀌었으면 승인하지 않고 다시 검토하게 함
 * fingerprint는 서버가 준 값을 그대로 보냄 (프런트가 계산하지 않음)
 */
export async function approve(projectId: string, choice: Choice): Promise<void> {
  const reviewed = plansByKey.get(keyOf(choice))
  if (!reviewed) throw new ApiError(404, '승인할 배포 계획을 찾지 못했습니다. 분석부터 다시 시작해 주세요.')

  if (await hasDeployment(projectId, reviewed.id)) return

  let plan = await req<PlanOut>(`/plans/${reviewed.id}`)
  if (plan.fingerprint !== reviewed.fingerprint)
    throw new ApiError(409, '검토한 뒤 계획 내용이 바뀌었습니다. 코드 검토 화면에서 다시 확인하고 승인해 주세요.')
  if (plan.status === 'awaiting_approval')
    plan = await req<PlanOut>(`/plans/${plan.id}/approve`, send('POST', { expected_fingerprint: plan.fingerprint }))
  else if (plan.status !== 'approved')
    throw new ApiError(409, `이 계획은 더 이상 승인할 수 없는 상태입니다(${plan.status}). 분석부터 다시 진행해 주세요.`)

  try {
    await req('/deployments', send('POST', { plan_id: plan.id, expected_fingerprint: plan.fingerprint }))
  } catch (e) {
    // 응답을 못 받았지만 실제로는 등록됐을 수 있음 → 서버에 있으면 성공으로 보고 상태 조회로
    if (await hasDeployment(projectId, plan.id).catch(() => false)) return
    throw e
  }
}

/** 실패 진단의 수정안을 반영하는 API는 아직 백엔드에 없음 */
export async function applyFix(_projectId: string, _choice: Choice): Promise<TerraformBundle> {
  throw new ApiError(501, '수정안 반영 API가 아직 백엔드에 없습니다. 원인을 고친 뒤 분석부터 다시 진행해 주세요.')
}

// ---------- 배포 상태, 이력 ----------

/**
 * 프로젝트의 최근 배포 상태. 대기열 등록 직후에는 배포 이력이 아직 없어 404일 수 있음 → 대기 중으로 표시
 * diagnosis는 화면 형태(cause, fix, patch)일 때만 씀
 */
export async function status(projectId: string): Promise<DeployStatus> {
  const s = await reqOrNull<{ state: DeployStatus['state']; log: string[]; url: string | null; diagnosis?: unknown }>(
    `/projects/${projectId}/status`,
  )
  if (!s) return { state: 'running', log: ['배포 대기열에 등록했습니다. worker가 가져가기를 기다리는 중입니다.'] }
  const domain = toDomainStatus((s as { domain?: unknown }).domain)
  const d = s.diagnosis
  const diagnosis =
    isObj(d) && typeof d.cause === 'string' && typeof d.fix === 'string' && isObj(d.patch)
      ? (d as unknown as DeployStatus['diagnosis'])
      : undefined
  return { state: s.state, log: s.log, url: s.url ?? undefined, diagnosis, domain }
}

/** 배포 이력. 백엔드가 화면용 요약 필드(app, tier, monthlyUsd 등)를 같이 줌 */
export async function history(): Promise<DeployRecord[]> {
  const rows = await req<(DeployRecord & { url: string | null })[]>('/deployments?limit=50')
  return rows.map((r) => ({
    id: r.id,
    app: r.app,
    version: r.version,
    tier: r.tier,
    provider: r.provider,
    target: r.target,
    monthlyUsd: r.monthlyUsd,
    status: r.status,
    url: r.url ?? undefined,
    createdAt: r.createdAt,
  }))
}

// ---------- 연결 ----------

export const listConnections = () => req<Connection[]>('/connections')

/** 저장된 연결 상태를 다시 읽음. 백엔드가 AWS를 실시간으로 검증하지는 않음 (worker가 완료를 기록) */
export const checkConnection = (id: string) => req<Connection>(`/connections/${id}/check`, { method: 'POST' })

export const deleteConnection = (id: string) => req<void>(`/connections/${id}`, { method: 'DELETE' })

/** 백엔드는 정의되지 않은 필드를 거절(422)하므로 id는 주소에만 넣고 본문에서 뺌 */
export function saveConnection(input: ConnectionInput): Promise<Connection> {
  const body = { provider: input.provider, name: input.name, fields: input.fields }
  return input.id
    ? req<Connection>(`/connections/${input.id}`, send('PUT', body))
    : req<Connection>('/connections', send('POST', body))
}

// ---------- 도메인 (제안 계약, 백엔드·인프라 구현 대기) ----------
// 서버에 아직 없으면 404/405가 오므로 501(미지원)로 바꿔 던짐 → 화면은 미리보기 주소로 진행

const DOMAIN_STATES = ['skipped', 'waiting_dns', 'issuing_cert', 'active', 'failed']

async function domainReq<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  try {
    return await req<T>(path, init)
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 405))
      throw new ApiError(501, '서버에 도메인 기능이 아직 없어 미리보기 주소(AWS 기본 주소)로 배포합니다. 도메인은 연결되지 않습니다.')
    throw e
  }
}

const toRecords = (v: unknown): DnsRecord[] =>
  (Array.isArray(v) ? v : []).filter(isObj).map((r) => ({
    type: str(r.type) as DnsRecord['type'],
    name: str(r.name),
    value: str(r.value),
    purpose: str(r.purpose),
  }))

/**
 * PUT /api/projects/{id}/domain { mode: auto|own, name } → { mode, name, monthly_usd, records, note }
 * auto 는 name 을 보내지 않고, 서버가 정한 자동 주소를 name 으로 돌려받음
 */
export async function saveDomain(projectId: string, choice: DomainChoice): Promise<DomainPlan> {
  const r = await domainReq<Record<string, unknown>>(
    `/projects/${projectId}/domain`,
    send('PUT', { mode: choice.mode, name: choice.mode === 'own' ? choice.name : null }),
  )
  return {
    mode: (str(r.mode) || choice.mode) as DomainPlan['mode'],
    name: r.name ? str(r.name) : null,
    monthlyUsd: Number(r.monthly_usd ?? 0),
    records: toRecords(r.records),
    note: r.note ? str(r.note) : undefined,
  }
}

function toDomainStatus(v: unknown): DomainStatus | undefined {
  if (!isObj(v) || !DOMAIN_STATES.includes(str(v.state))) return undefined
  return {
    state: str(v.state) as DomainStatus['state'],
    name: v.name ? str(v.name) : null,
    message: v.message ? str(v.message) : undefined,
    records: toRecords(v.records),
    url: v.url ? str(v.url) : undefined,
  }
}

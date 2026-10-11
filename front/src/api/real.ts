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
  Provider,
  Recommendation,
  ScaleInput,
  Source,
  TerraformBundle,
  Tier,
  TierKey,
} from '../types'
import { ApiError, req, reqOrNull, send } from './http'
import { sourceName, TIER_META, usd } from '../format'
import { isEnabled } from '../providers'

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

  const d = isObj(r.dockerfile_draft) ? r.dockerfile_draft : null
  const dockerfileDraft =
    d && typeof d.content === 'string' && d.content
      ? { content: d.content, port: Number(d.port) || 0, basedOn: Array.isArray(d.based_on) ? d.based_on.map(str) : [] }
      : undefined

  return { projectId: out.project_id, stack, findings, evidence, blockers: blockersOf(r, !!dockerfileDraft), dockerfileDraft }
}

// worker가 계획을 만들려면 꼭 있어야 하는 값 (infra/worker app_config_from_analysis)
const REQUIRED_FOR_PLAN: Record<string, string> = {
  container_port: '포트',
  health_check_path: '헬스체크 경로',
  dockerfile: 'Dockerfile',
}

/** 배포할 수 없는 이유. 미지원(supported: false)과, 계획에 꼭 필요한 값을 못 찾은 경우 */
function blockersOf(r: Record<string, unknown>, hasDraft: boolean): string[] {
  const out: string[] = []
  if (r.supported === false) {
    const reasons = Array.isArray(r.unsupported_reasons) ? r.unsupported_reasons.map(str).filter(Boolean) : []
    out.push(...(reasons.length ? reasons : ['지원하지 않는 형태의 앱입니다.']))
  }
  const unresolved = isObj(r.unresolved) ? r.unresolved : {}
  for (const [key, label] of Object.entries(REQUIRED_FOR_PLAN)) {
    // Dockerfile 초안이 있으면 Dockerfile·포트는 초안을 넣으면 해결됨(포트는 초안의 EXPOSE) → 오류가 아니라 남은 단계로 안내
    if (hasDraft && (key === 'dockerfile' || key === 'container_port')) continue
    if (key in unresolved) out.push(`${label}: ${str(unresolved[key]) || '코드에서 찾지 못했습니다.'}`)
  }
  return out
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

export interface PlanOut {
  id: string
  project_id: string
  analysis_id: string | null
  /** aws: Terraform 저장 plan, local_docker: 발표자 PC의 Docker로 올리는 compose 파일 (화면에서는 온프레미스) */
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
// 승인 금액. worker는 부하가 최대일 때(태스크 max_tasks개)의 월 비용을 넣음(cost.amount_basis: peak_monthly)
const planCost = (p: PlanOut) => Number(p.cost_estimate?.amount ?? 0)

const RANK_LABEL: Record<string, string> = { lowest: '최저', average: '평균', highest: '최대' }

/**
 * worker가 계획에 담아 준 비교 안(variables.options: 최저·평균·최대, 예산에 따라 1~3개).
 * Terraform 계획은 추천 안 하나만 있으므로 그 안은 계획에서, 나머지는 비교용 예상치로 만듦
 */
function tiersFromOptions(p: PlanOut): { key: TierKey; tier: Tier }[] {
  const options = Array.isArray(p.variables.options) ? p.variables.options.filter(isObj) : []
  const seen = new Set<TierKey>()
  const out: { key: TierKey; tier: Tier }[] = []
  for (const o of options) {
    const key = TIER_KEYS.find((k) => k === o.tier)
    if (!key || seen.has(key)) continue
    seen.add(key)
    const peak = Number(o.peak_monthly)
    const usual = Number(o.total_monthly)
    const extra = {
      rankLabel: RANK_LABEL[str(o.rank)],
      usualUsd: Number.isFinite(usual) ? usual : undefined,
    }
    if (o.recommended === true || key === p.variables.tier) {
      out.push({ key, tier: { ...toTier(p, key), ...extra, planned: true } })
      continue
    }
    const sizing = `${str(o.task_size)} 태스크 ${o.min_tasks}~${o.max_tasks}개`
    out.push({
      key,
      tier: {
        key,
        ...TIER_META[key],
        label: str(o.label) || TIER_META[key].label,
        headline: str(o.headline) || sizing,
        tradeoff: str(o.tradeoff),
        resources: [{ service: 'ECS Fargate', spec: sizing, monthlyUsd: Number.isFinite(peak) ? peak : 0, why: '비교용 예상치 (이 안의 배포 계획은 아직 없음)' }],
        totalUsd: Number.isFinite(peak) ? peak : undefined,
        ...extra,
        planned: false,
      },
    })
  }
  return out
}

const LOCAL = 'local_docker'
/** 온프레미스(로컬 Docker)는 연결 기록이 없어서 화면용 고정 id를 씀. 백엔드의 연결(connections)과 무관 */
const LOCAL_CONNECTION_ID = 'local-docker'
const isLocal = (p: PlanOut) => p.target === LOCAL
/** 먼저 온 쪽 계획만 있을 때 다른 대상의 계획을 더 기다리는 시간 (AWS 계획은 만드는 데 30초쯤 걸림) */
const OTHER_TARGET_GRACE_MS = 90 * 1000

// 화면에서 고른 칸(대상:크기) → 백엔드 계획. 승인할 때 plan id와 fingerprint를 여기서 찾음
let plansByKey = new Map<string, PlanOut>()

/** 계획 변수 중 화면 문구용 키를 빼고 짧게 요약 */
const META_KEYS = ['tier', 'recommended', 'headline', 'tradeoff', 'reason', 'resources', 'connection_name', 'usage_note']
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
    // 추가 비용이 없는 온프레미스는 금액 대신 자원 한도를 보여 줌
    usageNote: str(v.usage_note) || undefined,
    // 화면의 모든 금액·예산 판단은 서버 추정 총액 하나로 (resources 합계와 달라도)
    totalUsd: planCost(p),
  }
}

/** 소스에 Dockerfile이 없어 worker가 초안으로 빌드하는 계획이면, 초안을 코드 검토 탭 맨 앞에 보여 줌 (승인 대상) */
function draftFile(p: PlanOut): { name: string; content: string }[] {
  const d = p.variables.dockerfile_draft
  return isObj(d) && typeof d.content === 'string' && d.content ? [{ name: 'Dockerfile (초안)', content: d.content }] : []
}

/** 계획 금액에 넣지 않은 비용과 공용 기반 몫 (variables.cost). 없거나 형식이 다르면 비움 */
function costScope(p: PlanOut): { excludedCosts?: string[]; sharedMonthlyUsd?: number } {
  const c = isObj(p.variables.cost) ? p.variables.cost : {}
  const excluded = Array.isArray(c.excluded) ? c.excluded.map(str).filter(Boolean) : []
  const shared = Number(c.shared_monthly)
  return {
    excludedCosts: excluded.length ? excluded : undefined,
    sharedMonthlyUsd: c.shared_monthly != null && Number.isFinite(shared) ? shared : undefined,
  }
}

/** 온프레미스(로컬 Docker) 계획: 검토·승인 대상은 compose 파일과 실행 변수. compose의 SHA-256이 백엔드의 승인 식별자(fingerprint)에 들어 있음 */
export function toComposeBundle(p: PlanOut): TerraformBundle {
  const v = p.variables
  const containers = Array.isArray(v.resources) ? v.resources.length : null
  return {
    tool: 'compose',
    files: [
      { name: 'docker-compose.yml', content: str(v.compose_yaml) },
      { name: 'local-plan.variables.json', content: JSON.stringify(v, null, 2) },
    ],
    plan: {
      add: containers,
      change: containers === null ? null : 0,
      destroy: containers === null ? null : 0,
      text: [`# module: ${p.module_id}`, `# compose sha256: ${p.terraform_plan_sha256 ?? '(아직 없음)'}`, '', p.summary].join('\n'),
    },
    planInfo: {
      planId: p.id,
      moduleId: p.module_id,
      summary: p.summary,
      fingerprint: p.fingerprint,
      ready: p.terraform_plan_ready,
      pricingAsOf: undefined,
      ...costScope(p),
    },
  }
}

function toBundle(p: PlanOut): TerraformBundle {
  if (isLocal(p)) return toComposeBundle(p)
  return {
    tool: 'terraform',
    // AGENTS.md: 제품 LLM은 Terraform 변수값까지만 바꿈 → 검토 대상은 변수값과 계획 설명
    files: [
      ...draftFile(p),
      { name: 'variables.auto.tfvars.json', content: JSON.stringify(p.variables, null, 2) },
    ],
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
      ...costScope(p),
    },
  }
}

/**
 * 켜 둔 배포 대상(VITE_PROVIDERS)의 승인 대기 계획을 기다림. 모든 대상의 계획이 오면 바로, 한쪽만 먼저 왔으면 다른 쪽을
 * OTHER_TARGET_GRACE_MS 동안 더 기다린 뒤 있는 것만으로 진행 (한 대상의 계획 생성이 막혀도 다른 대상으로 배포할 수 있게)
 * AWS만 켠 기본 설정은 예전처럼 AWS 계획이 오면 바로 끝남
 */
async function waitForPlans(projectId: string, want: { aws: boolean; local: boolean }): Promise<PlanOut[]> {
  const timeoutMs = 5 * 60 * 1000
  const until = Date.now() + timeoutMs
  let firstSeen = 0
  for (;;) {
    const list = await req<PlanOut[]>(`/projects/${projectId}/plans`)
    const waiting = list.filter(
      (p) => p.status === 'awaiting_approval' && (isLocal(p) ? want.local : want.aws && p.target === 'aws'),
    )
    if (waiting.length) {
      if (!firstSeen) firstSeen = Date.now()
      const complete = (!want.aws || waiting.some((p) => !isLocal(p))) && (!want.local || waiting.some(isLocal))
      if (complete || Date.now() - firstSeen > OTHER_TARGET_GRACE_MS) return waiting
    }
    if (Date.now() > until)
      throw new ApiError(
        408,
        `${Math.round(timeoutMs / 60000)}분 동안 기다렸지만 배포 계획이(가) 오지 않았습니다. 배포 계획은 인프라 worker(infra/worker)가 만듭니다. worker가 실행 중인지 확인한 뒤 다시 기다려 주세요.`,
      )
    await wait(2000)
  }
}

/**
 * 계획 모듈이 승인 대기 계획을 만들어 둘 때까지 기다린 뒤 비교표로 바꿈.
 * 크기는 계획 변수의 tier를 쓰고, 없으면 싼 순서대로 작게 시작 / 권장 / 여유 있게
 * 월 예산을 넘는 계획은 추천하지 않음 (화면에서도 고를 수 없음)
 */
export async function recommend(projectId: string, scale: ScaleInput): Promise<Recommendation> {
  const [plans, conns] = await Promise.all([
    waitForPlans(projectId, { aws: isEnabled('aws'), local: isEnabled('onprem') }),
    req<Connection[]>('/connections'),
  ])

  const aws = conns.find((c) => c.provider === 'aws' && c.status === 'connected')
  const awsId = aws?.id ?? 'aws'
  const localName = str(plans.find(isLocal)?.variables.connection_name) || '이 PC (로컬 Docker)'

  // 대상마다 한 칸씩. AWS는 연결이 없으면 worker가 쓰는 운영자 AWS (관리형 배포), 온프레미스는 worker가 도는 PC의 로컬 Docker
  const groups: { connectionId: string; provider: Provider; name: string; plans: PlanOut[] }[] = [
    { connectionId: awsId, provider: 'aws', name: aws?.name ?? '관리형 AWS', plans: plans.filter((p) => !isLocal(p)) },
    { connectionId: LOCAL_CONNECTION_ID, provider: 'onprem', name: localName, plans: plans.filter(isLocal) },
  ]

  plansByKey = new Map()
  const bundles: Record<string, TerraformBundle> = {}
  const options: Recommendation['options'] = []
  const cells: { choice: Choice; plan: PlanOut }[] = []
  for (const g of groups) {
    if (!g.plans.length) continue
    // 크기별로 가장 최근 계획 하나씩
    const byTier = new Map<TierKey, PlanOut>()
    const sorted = [...g.plans].sort((a, b) => planCost(a) - planCost(b))
    sorted.forEach((p, i) => {
      const declared = TIER_KEYS.find((k) => k === p.variables.tier)
      const key = declared ?? TIER_KEYS[Math.min(i, TIER_KEYS.length - 1)]
      const prev = byTier.get(key)
      if (!prev || prev.created_at < p.created_at) byTier.set(key, p)
    })
    const tiers: Tier[] = []
    // 대상별 최신 계획의 비교 안을 유지하되, 실제 계획이 있는 칸만 승인·추천 대상으로 등록
    const newest = [...g.plans].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
    const fromOptions = tiersFromOptions(newest)
    if (fromOptions.length) {
      for (const { key, tier } of fromOptions.sort((a, b) => TIER_KEYS.indexOf(a.key) - TIER_KEYS.indexOf(b.key))) {
        tiers.push(tier)
        if (!tier.planned) continue
        const choice = { connectionId: g.connectionId, tier: key }
        const k = keyOf(choice)
        plansByKey.set(k, newest)
        bundles[k] = toBundle(newest)
        cells.push({ choice, plan: newest })
      }
    } else {
      for (const key of TIER_KEYS) {
        const p = byTier.get(key)
        if (!p) continue
        const choice = { connectionId: g.connectionId, tier: key }
        const k = keyOf(choice)
        plansByKey.set(k, p)
        bundles[k] = toBundle(p)
        tiers.push(toTier(p, key))
        cells.push({ choice, plan: p })
      }
    }
    options.push({ connectionId: g.connectionId, provider: g.provider, name: g.name, tiers })
  }

  const budget = scale.monthlyBudgetUsd
  const within = cells.filter((c) => planCost(c.plan) <= budget)
  // 기본은 AWS 계획. 월 예산 안에 드는 AWS 계획이 없을 때만 추가 비용이 없는 온프레미스를 추천
  const awsExists = cells.some((c) => !isLocal(c.plan))
  const awsWithin = within.filter((c) => !isLocal(c.plan))
  const pool = awsWithin.length ? awsWithin : within
  const pick = pool.find((c) => c.plan.variables.recommended === true) ?? [...pool].sort((a, b) => planCost(a.plan) - planCost(b.plan))[0]
  const cheapest = Math.min(...cells.map((c) => planCost(c.plan)))

  const awsPlan = plans.find((p) => !isLocal(p) && p.cost_estimate)
  const pricing = awsPlan?.cost_estimate?.pricing_as_of
  const scope = awsPlan ? costScope(awsPlan) : {}
  const assumptions = [`월 사용자 ${scale.expectedUsers}명, 월 예산 ${usd(budget)}`]
  if (awsExists) {
    assumptions.push(pricing ? `가격 기준: ${pricing}` : '가격 기준일 정보 없음')
    assumptions.push(
      scope.sharedMonthlyUsd !== undefined
        ? `AWS 금액에는 공용 기반(ALB·DB 등) 몫 월 ${usd(scope.sharedMonthlyUsd)}가 들어 있음`
        : '공유 기반(foundation) 비용 포함 여부는 계획 설명을 확인',
    )
    if (scope.excludedCosts) assumptions.push(`AWS 금액에 넣지 않은 비용: ${scope.excludedCosts.join(', ')}`)
  }
  if (cells.some((c) => isLocal(c.plan)))
    assumptions.push('온프레미스(로컬 Docker)의 추가 비용 0에는 전기·회선·PC 사용 비용이 들어 있지 않고, 이 PC의 HTTP 주소로만 접속합니다(도메인·HTTPS 없음)')
  const fallback = pick && awsExists && !awsWithin.length
  return {
    recommended: pick ? pick.choice : null,
    reason: pick
      ? (fallback ? `월 예산 ${usd(budget)} 안에 드는 AWS 계획이 없어 추가 비용이 없는 온프레미스를 추천합니다. ` : '') +
        (str(pick.plan.variables.reason) || pick.plan.summary)
      : `월 예산 ${usd(budget)} 안에 맞는 배포 계획이 없습니다. 가장 싼 계획도 월 ${usd(cheapest)}입니다. 예산을 늘려 주세요.`,
    options,
    assumptions,
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
      throw new ApiError(501, '서버에 도메인 기능이 아직 없어 미리보기 주소(AWS 기본 주소 또는 이 PC의 로컬 주소)로 배포합니다. 도메인은 연결되지 않습니다.')
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

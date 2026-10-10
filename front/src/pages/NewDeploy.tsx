import { useEffect, useRef, useState } from 'react'
import { api, ApiError, NEEDS_CONNECTION, sourceName } from '../api'
import StepRail, { type RailItem } from '../components/StepRail'
import ProviderMark from '../components/ProviderMark'
import SourceStep from '../steps/SourceStep'
import ScaleStep, { budgetValid } from '../steps/ScaleStep'
import AnalysisStep, { type CodeState } from '../steps/AnalysisStep'
import ReviewStep from '../steps/ReviewStep'
import DeployStep from '../steps/DeployStep'
import DomainStep, { domainReady } from '../steps/DomainStep'
import { domainDone } from '../components/DomainProgress'
import { costText, tierTotal, usd } from '../format'
import type { PollIssue } from '../steps/DeployStep'
import type {
  Analysis,
  Choice,
  Connection,
  DeployStatus,
  DomainChoice,
  DomainPlan,
  DomainQuote,
  Recommendation,
  ScaleInput,
  Source,
  TerraformBundle,
} from '../types'

// 앱 제출 → 도메인 → 분석과 추천 → 비용 승인 → 진행 상태
const SOURCE = 0
const DOMAIN = 1
const ANALYSIS = 2
const REVIEW = 3
const DEPLOY = 4

const STEPS = [
  { label: '앱 제출', title: '앱 제출', desc: '코드와 대략적인 사용 규모, 월 예산을 받습니다.' },
  { label: '도메인', title: '도메인', desc: '가지고 있는 도메인을 연결하거나, 새로 사거나, 나중에 정할 수 있습니다.' },
  { label: '분석과 추천', title: '분석과 추천 구성', desc: '코드에서 찾은 내용과 구성별 비용을 비교합니다.' },
  {
    label: '비용 승인',
    title: '비용 승인',
    desc: '앱과 도메인 비용, 배포 계획을 확인하고 승인합니다. 승인하기 전에는 아무것도 만들거나 사지 않습니다.',
  },
  { label: '진행 상태', title: '진행 상태', desc: '빌드·배포·헬스체크와 도메인 연결(DNS, 인증서) 진행 상황입니다.' },
]

const DEFAULT_DOMAIN: DomainChoice = { mode: 'later', name: '' }

const DEFAULT_SCALE: ScaleInput = { expectedUsers: '~1,000', pattern: 'unknown', purpose: '', monthlyBudgetUsd: 30 }

// 상태 확인 간격과, 일시적인 오류를 몇 번까지 다시 시도할지
const POLL_MS = 700
const MAX_POLL_ERRORS = 3
// 배포 상태 확인 기한. 넘으면 실패로 단정하지 않고 "확인 지연"으로 안내하고, 사용자가 다시 조회하게 함
// (자동으로 다시 배포하지 않음 → 중복 배포 방지)
const FIRST_DEADLINE_MS = 30 * 60 * 1000
const REPOLL_DEADLINE_MS = 10 * 60 * 1000
// 코드 검토 중 plan 파일 준비 여부를 다시 확인하는 간격
const PLAN_READY_POLL_MS = 3000
// 앱 배포가 끝난 뒤 도메인(DNS·인증서) 상태를 확인하는 간격
const DOMAIN_POLL_MS = 3000

type Busy = null | 'analyze' | 'approve' | 'fix'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const keyOf = (c: Choice) => `${c.connectionId}:${c.tier}`

interface Props {
  connections: Connection[]
  connectionsError: string | null
  onReloadConnections: () => void
  onConnectionsChange: (list: Connection[]) => void
  onShowHistory: () => void
  onShowConnections: () => void
}

export default function NewDeploy({
  connections,
  connectionsError,
  onReloadConnections,
  onConnectionsChange,
  onShowHistory,
  onShowConnections,
}: Props) {
  const [step, setStep] = useState(SOURCE)
  const [source, setSourceState] = useState<Source | null>(null)
  const [scale, setScaleState] = useState<ScaleInput>(DEFAULT_SCALE)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [rec, setRec] = useState<Recommendation | null>(null)
  const [choice, setChoiceState] = useState<Choice | null>(null)
  // 조합별로 만들어 둔 코드. 다른 칸을 눌렀다 돌아와도 다시 만들지 않음
  const [bundles, setBundles] = useState<Record<string, TerraformBundle>>({})
  const [codeErrors, setCodeErrors] = useState<Record<string, string>>({})
  const [inflight, setInflight] = useState<Set<string>>(new Set())
  const [confirmed, setConfirmed] = useState(false)
  const [domainChoice, setDomainChoiceState] = useState<DomainChoice>(DEFAULT_DOMAIN)
  const [domainQuote, setDomainQuote] = useState<DomainQuote | null>(null)
  // 서버가 확정한 도메인 계획. 분석(프로젝트 생성) 뒤에 저장
  const [domainPlan, setDomainPlan] = useState<DomainPlan | null>(null)
  const [domainNotice, setDomainNotice] = useState<string | null>(null)
  const [purchaseConfirmed, setPurchaseConfirmed] = useState(false)
  const [approved, setApproved] = useState(false)
  const [deploy, setDeploy] = useState<DeployStatus | null>(null)
  // 올릴 때마다 상태 확인을 새로 시작 (새 배포, 다시 조회)
  const [pollRound, setPollRound] = useState(0)
  const [pollIssue, setPollIssue] = useState<PollIssue | null>(null)
  const deadlineRef = useRef(0)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  // 분석을 새로 하면 이전 분석의 늦게 도착한 코드 응답은 버림
  const projectRef = useRef<string | null>(null)

  const resetResults = () => {
    projectRef.current = null
    setAnalysis(null)
    setRec(null)
    setChoiceState(null)
    setBundles({})
    setCodeErrors({})
    setInflight(new Set())
    setConfirmed(false)
    setDomainPlan(null)
    setDomainNotice(null)
    setPurchaseConfirmed(false)
  }
  const setSource = (s: Source | null) => {
    setSourceState(s)
    resetResults()
  }
  const setScale = (s: ScaleInput) => {
    setScaleState(s)
    if (analysis) resetResults()
  }
  // 도메인을 바꾸면 분석은 그대로 두고 도메인 계획만 다시 저장
  const setDomainChoice = (c: DomainChoice) => {
    setDomainChoiceState(c)
    setDomainPlan(null)
    setDomainNotice(null)
    setPurchaseConfirmed(false)
  }

  const prefetch = (projectId: string, c: Choice) => {
    const key = keyOf(c)
    if (bundles[key] || inflight.has(key)) return
    setInflight((s) => new Set(s).add(key))
    setCodeErrors(({ [key]: _, ...rest }) => rest)
    api
      .generate(projectId, c)
      .then((b) => {
        if (projectRef.current === projectId) setBundles((m) => ({ ...m, [key]: b }))
      })
      .catch((e) => {
        if (projectRef.current === projectId) setCodeErrors((m) => ({ ...m, [key]: errMsg(e) }))
      })
      .finally(() =>
        setInflight((s) => {
          const n = new Set(s)
          n.delete(key)
          return n
        }),
      )
  }

  const setChoice = (c: Choice) => {
    setChoiceState(c)
    setConfirmed(false)
    // 고르는 순간 뒤에서 코드 생성 시작 → 코드 검토로 넘어갈 때는 대부분 준비돼 있음
    if (analysis) prefetch(analysis.projectId, c)
  }

  const key = choice ? keyOf(choice) : null
  const bundle = key ? bundles[key] ?? null : null
  const codeState: CodeState = !key
    ? 'idle'
    : bundle
      ? 'ready'
      : inflight.has(key)
        ? 'loading'
        : codeErrors[key]
          ? 'error'
          : 'idle'

  const reached = approved
    ? DEPLOY
    : bundle && domainPlan
      ? REVIEW
      : analysis && rec && domainPlan
        ? ANALYSIS
        : source
          ? DOMAIN
          : SOURCE
  const locked = approved

  const option = rec?.options.find((o) => o.connectionId === choice?.connectionId) ?? null
  const selectedTier = option?.tiers.find((t) => t.key === choice?.tier) ?? null
  const usable = connections.filter((c) => c.status === 'connected')

  // 승인 후 상태 확인. 끝나거나(성공·실패), 기한을 넘기거나, 화면을 떠나거나, 새로 조회하면 멈춤
  // 기한 초과·조회 실패는 배포 실패가 아님 → pollIssue로 안내만 하고 다시 조회는 사용자가 누름
  useEffect(() => {
    if (!pollRound || !analysis) return
    let stopped = false
    let timer: number | undefined
    let errors = 0
    setPollIssue(null)
    const tick = async () => {
      try {
        const s = await api.status(analysis.projectId)
        if (stopped) return
        errors = 0
        setDeploy(s)
        if (s.state !== 'running') setBusy(null)
        // 앱이 끝나도 도메인(DNS·인증서)이 진행 중이면 계속 확인
        if (s.state === 'failed' || (s.state === 'success' && domainDone(s.domain))) return
        if (Date.now() > deadlineRef.current) {
          setPollIssue({
            kind: 'timeout',
            message: '예상보다 오래 걸리고 있습니다. 배포는 서버에서 계속 진행 중일 수 있습니다.',
          })
          return setBusy(null)
        }
        timer = window.setTimeout(tick, s.state === 'running' ? POLL_MS : DOMAIN_POLL_MS)
      } catch (e) {
        if (stopped) return
        errors += 1
        if (errors < MAX_POLL_ERRORS) {
          // 일시적인 네트워크 오류는 점점 늦춰 가며 다시 확인
          timer = window.setTimeout(tick, POLL_MS * 2 ** errors)
          return
        }
        setPollIssue({ kind: 'error', message: errMsg(e) })
        setBusy(null)
      }
    }
    tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [pollRound])

  const startPolling = (deadlineMs: number) => {
    deadlineRef.current = Date.now() + deadlineMs
    setPollRound((n) => n + 1)
  }

  // 상태만 다시 읽음. 배포를 다시 요청하지 않음
  const repoll = () => {
    setBusy('approve')
    startPolling(REPOLL_DEADLINE_MS)
  }

  // 코드 검토 중 plan 파일이 아직 없으면, LLM을 다시 돌리지 않고 같은 계획의 준비 상태만 다시 읽음
  const planReady = bundle?.planInfo?.ready
  useEffect(() => {
    if (step !== REVIEW || locked || !analysis || !choice || planReady !== false) return
    let stopped = false
    let timer: number | undefined
    const reviewedFingerprint = bundle?.planInfo?.fingerprint
    const tick = async () => {
      try {
        const fresh = await api.generate(analysis.projectId, choice)
        if (stopped) return
        setBundles((m) => ({ ...m, [keyOf(choice)]: fresh }))
        // 계획 내용이 바뀌었으면 이전 확인은 무효
        if (fresh.planInfo?.fingerprint !== reviewedFingerprint) setConfirmed(false)
        if (fresh.planInfo?.ready) return
      } catch {
        // 일시적인 오류면 다음에 다시
      }
      if (!stopped) timer = window.setTimeout(tick, PLAN_READY_POLL_MS)
    }
    timer = window.setTimeout(tick, PLAN_READY_POLL_MS)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [step, locked, key, planReady])

  const run = async (kind: Exclude<Busy, null>, fn: () => Promise<void>) => {
    setBusy(kind)
    setError(null)
    try {
      await fn()
      // approve는 폴링이 끝날 때 busy를 푼다
      if (kind !== 'approve') setBusy(null)
    } catch (e) {
      setError(errMsg(e))
      setBusy(null)
    }
  }

  // 도메인 계획 저장. 서버에 도메인 기능이 없으면(501) "나중에"로 진행하고 이유를 보여 줌
  const saveDomain = async (projectId: string) => {
    try {
      setDomainPlan(await api.saveDomain(projectId, domainChoice))
      setDomainNotice(null)
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 501)) throw e
      setDomainPlan({ mode: 'later', name: null, oneTimeUsd: 0, monthlyUsd: 0, records: [] })
      setDomainNotice(e.message)
    }
  }

  // 분석 → 추천(+추천 조합 코드) → 도메인 계획 저장을 한 번에
  // 이미 분석했으면 바뀐 도메인만 다시 저장
  const analyze = () =>
    run('analyze', async () => {
      if (!source) return
      if (analysis && rec) {
        if (!domainPlan) await saveDomain(analysis.projectId)
      } else {
        const a = await api.analyze(source, scale)
        const r = await api.recommend(a.projectId, scale)
        projectRef.current = a.projectId
        setAnalysis(a)
        setRec(r)
        setBundles(r.bundles ?? {})
        // 예산 안에 맞는 구성이 없으면 recommended가 null → 화면에 이유만 보여 줌
        setChoiceState(r.recommended)
        // 백엔드가 추천 조합 코드를 안 보냈으면 바로 요청
        if (r.recommended && !r.bundles?.[keyOf(r.recommended)]) prefetch(a.projectId, r.recommended)
        await saveDomain(a.projectId)
      }
      setStep(ANALYSIS)
    })

  const approve = () =>
    run('approve', async () => {
      if (!analysis || !choice) return
      // 도메인 구매는 사용자가 본 금액으로 먼저 확정 (환불 불가라 앱 승인과 따로 동의받음)
      if (domainPlan?.mode === 'buy') await api.confirmDomainPurchase(analysis.projectId, domainPlan)
      await api.approve(analysis.projectId, choice)
      setApproved(true)
      setDeploy(null)
      setStep(DEPLOY)
      startPolling(FIRST_DEADLINE_MS)
    })

  // 실패 진단의 수정안 반영 → 검증·plan 재생성 → 코드 검토로 돌아가 다시 승인
  // 승인한 내용이 바뀌었으므로 이전 승인은 버림
  const applyFix = () =>
    run('fix', async () => {
      if (!analysis || !choice) return
      const fixed = await api.applyFix(analysis.projectId, choice)
      setBundles((m) => ({ ...m, [keyOf(choice)]: fixed }))
      setApproved(false)
      setConfirmed(false)
      setDeploy(null)
      setPollIssue(null)
      setStep(REVIEW)
    })

  const restart = () => {
    setPollIssue(null)
    setStep(SOURCE)
    setSourceState(null)
    setDomainChoiceState(DEFAULT_DOMAIN)
    setDomainQuote(null)
    setScaleState(DEFAULT_SCALE)
    resetResults()
    setApproved(false)
    setDeploy(null)
    setError(null)
  }

  const domainLabel =
    domainChoice.mode === 'later'
      ? '나중에'
      : `${domainChoice.name || '이름 미입력'} (${domainChoice.mode === 'buy' ? '구매' : '보유'})`

  const railItems: RailItem[] = STEPS.map((s, i) => {
    let sub: string | undefined
    if (i === SOURCE && source)
      sub = `${sourceName(source)} · 월 ${scale.expectedUsers}명 · ${budgetValid(scale.monthlyBudgetUsd) ? usd(scale.monthlyBudgetUsd) : '예산 미입력'}`
    if (i === DOMAIN && source) sub = domainLabel
    if (i === ANALYSIS && option && selectedTier) sub = `${option.name} · ${selectedTier.label}`
    if (i === REVIEW && bundle) sub = bundle.plan.add === null ? '계획 준비됨' : `${bundle.plan.add}개 추가`
    if (i === REVIEW && !bundle && codeState === 'loading') sub = '코드 준비 중'
    if (i === DEPLOY && deploy) sub = { running: '진행 중', success: '완료', failed: '실패' }[deploy.state]
    if (i === DEPLOY && deploy?.state === 'success' && !domainDone(deploy.domain)) sub = '도메인 연결 중'

    let state: RailItem['state'] = 'todo'
    if (i === DEPLOY && deploy?.state === 'failed') state = 'failed'
    else if (i === step) state = 'current'
    else if (i < reached || (i === DEPLOY && deploy?.state === 'success')) state = 'done'

    return { label: s.label, sub, state, enabled: i <= reached && busy === null }
  })

  const meta = STEPS[step]

  let next: { label: string; onClick: () => void; disabled?: boolean } | null = null
  if (step === SOURCE)
    next = {
      label: '다음: 도메인',
      onClick: () => setStep(DOMAIN),
      disabled:
        !source || !budgetValid(scale.monthlyBudgetUsd) || (NEEDS_CONNECTION && !analysis && usable.length === 0),
    }
  if (step === DOMAIN && !locked)
    next = {
      label: busy === 'analyze' ? '분석하고 계획 준비 중…' : analysis && domainPlan ? '다음' : '분석 시작',
      onClick: analyze,
      disabled: !domainReady(domainChoice, domainQuote),
    }
  if (step === DOMAIN && locked) next = { label: '다음', onClick: () => setStep(ANALYSIS) }
  if (step === ANALYSIS && !locked) {
    if (codeState === 'error' && analysis && choice)
      next = { label: '코드 다시 만들기', onClick: () => prefetch(analysis.projectId, choice) }
    else if (!choice) next = { label: '비용 승인', onClick: () => {}, disabled: true }
    else
      next = {
        label: codeState === 'loading' ? '계획 준비 중…' : '비용 승인',
        onClick: () => setStep(REVIEW),
        disabled: codeState !== 'ready',
      }
  }
  if (step === ANALYSIS && locked) next = { label: '다음', onClick: () => setStep(REVIEW) }
  if (step === REVIEW && !locked)
    next = {
      label:
        busy === 'approve' ? '승인 처리 중…' : domainPlan?.mode === 'buy' ? '도메인 구매하고 배포' : '승인하고 배포',
      onClick: approve,
      disabled: !confirmed || (domainPlan?.mode === 'buy' && !purchaseConfirmed),
    }
  if (step === REVIEW && locked) next = { label: '진행 상태로', onClick: () => setStep(DEPLOY) }

  return (
    <div className="deploy-layout">
      <aside className="deploy-side">
        <StepRail items={railItems} onSelect={setStep} />
        {option && selectedTier && (
          <div className="cost-note">
            <span>월 예상 비용</span>
            <strong>{costText(tierTotal(selectedTier))}</strong>
            <small className="with-mark">
              <ProviderMark provider={option.provider} /> {option.name} · {selectedTier.label}
            </small>
            <small className="muted">월 예산 {usd(scale.monthlyBudgetUsd)} 안</small>
            {domainPlan && domainPlan.mode !== 'later' && (
              <small>
                도메인 {domainPlan.name}
                {domainPlan.oneTimeUsd > 0 && ` · 1회 ${usd(domainPlan.oneTimeUsd)}`}
                {domainPlan.monthlyUsd > 0 && ` · 매달 +${usd(domainPlan.monthlyUsd)}`}
              </small>
            )}
            {rec && (rec.recommended?.connectionId !== option.connectionId || rec.recommended?.tier !== selectedTier.key) && (
              <small className="muted">AI 추천과 다른 선택</small>
            )}
          </div>
        )}
      </aside>

      <section className="panel">
        <header className="panel-head">
          <span className="eyebrow">
            {step + 1} / {STEPS.length}
          </span>
          <h1>{meta.title}</h1>
          <p>{meta.desc}</p>
        </header>

        {error && (
          <div className="error" role="alert">
            요청이 실패했습니다. {error}
          </div>
        )}

        <div className="panel-body">
          {domainNotice && (step === ANALYSIS || step === REVIEW) && <p className="readonly-note">{domainNotice}</p>}
          {step === SOURCE && (
            <div className="stack-lg">
              <section>
                <h3 className="sub-title">소스</h3>
                <SourceStep source={source} locked={locked || busy === 'analyze'} onSource={setSource} />
              </section>
              <section>
                <h3 className="sub-title">사용 규모</h3>
                <ScaleStep
                  scale={scale}
                  locked={locked || busy === 'analyze'}
                  connections={connections}
                  connectionsError={connectionsError}
                  onReloadConnections={onReloadConnections}
                  onChange={setScale}
                  onConnectionsChange={onConnectionsChange}
                  onShowConnections={onShowConnections}
                />
              </section>
            </div>
          )}
          {step === DOMAIN && (
            <DomainStep
              choice={domainChoice}
              quote={domainQuote}
              locked={locked || busy === 'analyze'}
              onChange={setDomainChoice}
              onQuote={setDomainQuote}
            />
          )}
          {step === ANALYSIS && analysis && rec && (
            <AnalysisStep
              analysis={analysis}
              rec={rec}
              choice={choice}
              budget={scale.monthlyBudgetUsd}
              codeState={codeState}
              codeError={key ? codeErrors[key] : undefined}
              locked={locked || busy !== null}
              onChoice={setChoice}
            />
          )}
          {step === REVIEW && bundle && option && selectedTier && (
            <ReviewStep
              key={key}
              bundle={bundle}
              tier={selectedTier}
              target={option}
              domain={domainPlan}
              confirmed={confirmed}
              purchaseConfirmed={purchaseConfirmed}
              locked={locked}
              onConfirm={setConfirmed}
              onPurchaseConfirm={setPurchaseConfirmed}
            />
          )}
          {step === DEPLOY && (
            <DeployStep
              status={deploy}
              targetName={option?.name ?? ''}
              domainMode={domainPlan?.mode ?? 'later'}
              fixing={busy === 'fix'}
              pollIssue={pollIssue}
              repolling={busy === 'approve'}
              onRepoll={repoll}
              onFix={applyFix}
              onRestart={restart}
              onHistory={onShowHistory}
            />
          )}
        </div>

        {step < DEPLOY && (
          <footer className="panel-foot">
            {step > SOURCE ? (
              <button className="btn btn-ghost" onClick={() => setStep(step - 1)} disabled={busy !== null}>
                이전
              </button>
            ) : (
              <span />
            )}
            {next && (
              <button className="btn btn-primary" onClick={next.onClick} disabled={next.disabled || busy !== null}>
                {next.label}
              </button>
            )}
          </footer>
        )}
      </section>
    </div>
  )
}

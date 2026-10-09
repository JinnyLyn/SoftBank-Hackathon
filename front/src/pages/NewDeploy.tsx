import { useEffect, useRef, useState } from 'react'
import { api, sourceName } from '../api'
import StepRail, { type RailItem } from '../components/StepRail'
import ProviderMark from '../components/ProviderMark'
import SourceStep from '../steps/SourceStep'
import ScaleStep, { budgetValid } from '../steps/ScaleStep'
import AnalysisStep, { type CodeState } from '../steps/AnalysisStep'
import ReviewStep from '../steps/ReviewStep'
import DeployStep from '../steps/DeployStep'
import { costText, tierTotal, usd } from '../format'
import type {
  Analysis,
  Choice,
  Connection,
  DeployStatus,
  Recommendation,
  ScaleInput,
  Source,
  TerraformBundle,
} from '../types'

const STEPS = [
  {
    label: '소스와 규모',
    title: '소스와 사용 규모',
    desc: '코드와 대략적인 사용 규모를 한 번에 받습니다. 분석, 구성 추천, 배포 코드까지 여기서 미리 만들어 둡니다.',
  },
  { label: '분석과 추천', title: '분석과 추천 구성', desc: '코드에서 찾은 내용과, 연결된 배포 대상별 구성과 비용을 나란히 비교합니다.' },
  { label: '코드 검토', title: '코드 검토', desc: '미리 만들어 둔 코드와 변경 계획입니다. 승인하기 전에는 아무것도 만들지 않습니다.' },
  { label: '배포', title: '배포', desc: '이미지를 빌드해 배포하고 헬스체크까지 확인합니다.' },
]

const DEFAULT_SCALE: ScaleInput = { expectedUsers: '~1,000', pattern: 'unknown', purpose: '', monthlyBudgetUsd: 30 }

// 상태 확인 간격과, 일시적인 오류를 몇 번까지 다시 시도할지
const POLL_MS = 700
const MAX_POLL_ERRORS = 3

type Busy = null | 'analyze' | 'approve' | 'fix'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const keyOf = (c: Choice) => `${c.connectionId}:${c.tier}`

interface Props {
  connections: Connection[]
  onConnectionsChange: (list: Connection[]) => void
  onShowHistory: () => void
  onShowConnections: () => void
}

export default function NewDeploy({ connections, onConnectionsChange, onShowHistory, onShowConnections }: Props) {
  const [step, setStep] = useState(0)
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
  const [approved, setApproved] = useState(false)
  const [deploy, setDeploy] = useState<DeployStatus | null>(null)
  const [runId, setRunId] = useState(0)
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
  }
  const setSource = (s: Source | null) => {
    setSourceState(s)
    resetResults()
  }
  const setScale = (s: ScaleInput) => {
    setScaleState(s)
    if (analysis) resetResults()
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

  const reached = approved ? 3 : bundle ? 2 : analysis && rec ? 1 : 0
  const locked = approved

  const option = rec?.options.find((o) => o.connectionId === choice?.connectionId) ?? null
  const selectedTier = option?.tiers.find((t) => t.key === choice?.tier) ?? null
  const usable = connections.filter((c) => c.status === 'connected')

  // 승인 후 상태 확인. 끝나거나(성공·실패) 화면을 떠나거나 새 배포가 시작되면 멈춤
  useEffect(() => {
    if (!runId || !analysis) return
    let stopped = false
    let timer: number | undefined
    let errors = 0
    const tick = async () => {
      try {
        const s = await api.status(analysis.projectId)
        if (stopped) return
        errors = 0
        setDeploy(s)
        if (s.state === 'running') timer = window.setTimeout(tick, POLL_MS)
        else setBusy(null)
      } catch (e) {
        if (stopped) return
        errors += 1
        if (errors < MAX_POLL_ERRORS) {
          // 일시적인 네트워크 오류는 점점 늦춰 가며 다시 확인
          timer = window.setTimeout(tick, POLL_MS * 2 ** errors)
          return
        }
        setError(`배포 상태를 가져오지 못했습니다. ${errMsg(e)}`)
        setBusy(null)
      }
    }
    tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [runId])

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

  // 분석 → 추천(+추천 조합 코드)을 한 번에
  const analyze = () =>
    run('analyze', async () => {
      if (!source) return
      if (!analysis || !rec) {
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
      }
      setStep(1)
    })

  const approve = () =>
    run('approve', async () => {
      if (!analysis || !choice) return
      await api.approve(analysis.projectId, choice)
      setApproved(true)
      setDeploy(null)
      setStep(3)
      setRunId((n) => n + 1)
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
      setStep(2)
    })

  const restart = () => {
    setStep(0)
    setSourceState(null)
    setScaleState(DEFAULT_SCALE)
    resetResults()
    setApproved(false)
    setDeploy(null)
    setError(null)
  }

  const railItems: RailItem[] = STEPS.map((s, i) => {
    let sub: string | undefined
    if (i === 0 && source) sub = `${sourceName(source)} · 월 ${scale.expectedUsers}명 · ${budgetValid(scale.monthlyBudgetUsd) ? usd(scale.monthlyBudgetUsd) : '예산 미입력'}`
    if (i === 1 && option && selectedTier) sub = `${option.name} · ${selectedTier.label}`
    if (i === 2 && bundle) sub = bundle.plan.add === null ? '계획 준비됨' : `${bundle.plan.add}개 추가`
    if (i === 2 && !bundle && codeState === 'loading') sub = '코드 준비 중'
    if (i === 3 && deploy) sub = { running: '진행 중', success: '완료', failed: '실패' }[deploy.state]

    let state: RailItem['state'] = 'todo'
    if (i === 3 && deploy?.state === 'failed') state = 'failed'
    else if (i === step) state = 'current'
    else if (i < reached || (i === 3 && deploy?.state === 'success')) state = 'done'

    return { label: s.label, sub, state, enabled: i <= reached && busy === null }
  })

  const meta = STEPS[step]

  let next: { label: string; onClick: () => void; disabled?: boolean } | null = null
  if (step === 0)
    next = {
      label: busy === 'analyze' ? '분석하고 코드 준비 중…' : analysis ? '다음' : '분석 시작',
      onClick: analyze,
      disabled: !source || !budgetValid(scale.monthlyBudgetUsd) || (!analysis && usable.length === 0),
    }
  if (step === 1 && !locked) {
    if (codeState === 'error' && analysis && choice)
      next = { label: '코드 다시 만들기', onClick: () => prefetch(analysis.projectId, choice) }
    else if (!choice) next = { label: '코드 검토', onClick: () => {}, disabled: true }
    else
      next = {
        label: codeState === 'loading' ? '코드 준비 중…' : '코드 검토',
        onClick: () => setStep(2),
        disabled: codeState !== 'ready',
      }
  }
  if (step === 1 && locked) next = { label: '다음', onClick: () => setStep(2) }
  if (step === 2 && !locked)
    next = { label: busy === 'approve' ? '승인 처리 중…' : '승인하고 배포', onClick: approve, disabled: !confirmed }
  if (step === 2 && locked) next = { label: '배포 화면으로', onClick: () => setStep(3) }

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
          {step === 0 && (
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
                  onChange={setScale}
                  onConnectionsChange={onConnectionsChange}
                  onShowConnections={onShowConnections}
                />
              </section>
            </div>
          )}
          {step === 1 && analysis && rec && (
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
          {step === 2 && bundle && option && selectedTier && (
            <ReviewStep
              key={key}
              bundle={bundle}
              tier={selectedTier}
              target={option}
              confirmed={confirmed}
              locked={locked}
              onConfirm={setConfirmed}
            />
          )}
          {step === 3 && (
            <DeployStep
              status={deploy}
              targetName={option?.name ?? ''}
              fixing={busy === 'fix'}
              onFix={applyFix}
              onRestart={restart}
              onHistory={onShowHistory}
            />
          )}
        </div>

        {step < 3 && (
          <footer className="panel-foot">
            {step > 0 ? (
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

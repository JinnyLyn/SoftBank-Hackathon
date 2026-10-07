import { useEffect, useState } from 'react'
import { api, sourceName } from '../api'
import StepRail, { type RailItem } from '../components/StepRail'
import ProviderMark from '../components/ProviderMark'
import SourceStep from '../steps/SourceStep'
import ScaleStep from '../steps/ScaleStep'
import AnalysisStep from '../steps/AnalysisStep'
import ReviewStep from '../steps/ReviewStep'
import DeployStep from '../steps/DeployStep'
import { costText, tierTotal } from '../format'
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
  { label: '소스', title: '소스 가져오기', desc: 'zip 파일을 올리거나 GitHub public 저장소 주소를 넣으세요.' },
  { label: '사용 규모', title: '사용 규모', desc: '대략적인 값이면 됩니다. 서버 크기와 비용을 고르는 데만 씁니다.' },
  { label: '분석과 추천', title: '분석과 추천 구성', desc: '코드에서 찾은 내용과, 연결된 배포 대상별 구성과 비용을 나란히 비교합니다.' },
  { label: '코드 검토', title: '코드 검토', desc: 'AI가 만든 코드와 변경 계획입니다. 승인하기 전에는 아무것도 만들지 않습니다.' },
  { label: '배포', title: '배포', desc: '이미지를 빌드해 배포하고 헬스체크까지 확인합니다.' },
]

const DEFAULT_SCALE: ScaleInput = { expectedUsers: '~1,000', pattern: 'unknown', purpose: '' }

type Busy = null | 'analyze' | 'generate' | 'approve'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

interface Props {
  connections: Connection[]
  userName: string
  onShowHistory: () => void
  onShowConnections: () => void
}

export default function NewDeploy({ connections, userName, onShowHistory, onShowConnections }: Props) {
  const [step, setStep] = useState(0)
  const [source, setSourceState] = useState<Source | null>(null)
  const [scale, setScaleState] = useState<ScaleInput>(DEFAULT_SCALE)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [rec, setRec] = useState<Recommendation | null>(null)
  const [choice, setChoiceState] = useState<Choice | null>(null)
  const [bundle, setBundle] = useState<TerraformBundle | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [approved, setApproved] = useState(false)
  const [deploy, setDeploy] = useState<DeployStatus | null>(null)
  const [runId, setRunId] = useState(0)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)

  // 앞 단계 입력이 바뀌면 뒤 단계 결과는 버림
  const clearFrom = (level: 'analysis' | 'bundle') => {
    if (level === 'analysis') {
      setAnalysis(null)
      setRec(null)
      setChoiceState(null)
    }
    setBundle(null)
    setConfirmed(false)
  }
  const setSource = (s: Source | null) => {
    setSourceState(s)
    clearFrom('analysis')
  }
  const setScale = (s: ScaleInput) => {
    setScaleState(s)
    if (analysis) clearFrom('analysis')
  }
  const setChoice = (c: Choice) => {
    setChoiceState(c)
    clearFrom('bundle')
  }

  const reached = approved ? 4 : bundle ? 3 : analysis && rec && choice ? 2 : source ? 1 : 0
  const locked = approved

  const option = rec?.options.find((o) => o.connectionId === choice?.connectionId) ?? null
  const selectedTier = option?.tiers.find((t) => t.key === choice?.tier) ?? null
  const usable = connections.filter((c) => c.status === 'connected')

  useEffect(() => {
    if (!runId || !analysis) return
    let stopped = false
    const tick = async () => {
      try {
        const s = await api.status(analysis.projectId)
        if (stopped) return
        setDeploy(s)
        if (s.state === 'running') setTimeout(tick, 700)
        else setBusy(null)
      } catch (e) {
        if (stopped) return
        setError(errMsg(e))
        setBusy(null)
      }
    }
    tick()
    return () => {
      stopped = true
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

  const analyze = () =>
    run('analyze', async () => {
      if (!source) return
      if (!analysis || !rec) {
        const a = await api.analyze(source, scale)
        const r = await api.recommend(a.projectId, scale)
        setAnalysis(a)
        setRec(r)
        setChoiceState(r.options.length ? r.recommended : null)
      }
      setStep(2)
    })

  const generate = () =>
    run('generate', async () => {
      if (!analysis || !choice) return
      if (!bundle) setBundle(await api.generate(analysis.projectId, choice))
      setStep(3)
    })

  const approve = () =>
    run('approve', async () => {
      if (!analysis || !choice) return
      await api.approve(analysis.projectId, choice)
      setApproved(true)
      setDeploy(null)
      setStep(4)
      setRunId((n) => n + 1)
    })

  const retry = () =>
    run('approve', async () => {
      if (!analysis || !choice) return
      await api.approve(analysis.projectId, choice)
      setRunId((n) => n + 1)
    })

  const restart = () => {
    setStep(0)
    setSourceState(null)
    setScaleState(DEFAULT_SCALE)
    clearFrom('analysis')
    setApproved(false)
    setDeploy(null)
    setError(null)
  }

  const railItems: RailItem[] = STEPS.map((s, i) => {
    let sub: string | undefined
    if (i === 0 && source) sub = sourceName(source)
    if (i === 1 && source) sub = `월 ${scale.expectedUsers}명`
    if (i === 2 && option && selectedTier) sub = `${option.name} · ${selectedTier.label}`
    if (i === 3 && bundle) sub = `${bundle.plan.add}개 추가`
    if (i === 4 && deploy) sub = { running: '진행 중', success: '완료', failed: '실패' }[deploy.state]

    let state: RailItem['state'] = 'todo'
    if (i === 4 && deploy?.state === 'failed') state = 'failed'
    else if (i === step) state = 'current'
    else if (i < reached || (i === 4 && deploy?.state === 'success')) state = 'done'

    return { label: s.label, sub, state, enabled: i <= reached && busy === null }
  })

  const meta = STEPS[step]

  let next: { label: string; onClick: () => void; disabled?: boolean } | null = null
  if (step === 0) next = { label: '다음', onClick: () => setStep(1), disabled: !source }
  if (step === 1)
    next = {
      label: busy === 'analyze' ? '코드 읽는 중…' : analysis ? '다음' : '분석하고 구성 추천받기',
      onClick: analyze,
      disabled: !analysis && usable.length === 0,
    }
  if (step === 2 && !locked)
    next = {
      label: busy === 'generate' ? '코드 작성 중…' : bundle ? '다음' : '배포 코드 만들기',
      onClick: generate,
      disabled: !choice,
    }
  if (step === 2 && locked) next = { label: '다음', onClick: () => setStep(3) }
  if (step === 3 && !locked)
    next = { label: busy === 'approve' ? '승인 처리 중…' : '승인하고 배포', onClick: approve, disabled: !confirmed }
  if (step === 3 && locked) next = { label: '배포 화면으로', onClick: () => setStep(4) }

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
            {rec && (rec.recommended.connectionId !== option.connectionId || rec.recommended.tier !== selectedTier.key) && (
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
          {step === 0 && <SourceStep source={source} locked={locked} onSource={setSource} />}
          {step === 1 && (
            <ScaleStep
              scale={scale}
              locked={locked || busy === 'analyze'}
              connections={connections}
              onChange={setScale}
              onShowConnections={onShowConnections}
            />
          )}
          {step === 2 && analysis && rec && choice && (
            <AnalysisStep analysis={analysis} rec={rec} choice={choice} locked={locked || busy !== null} onChoice={setChoice} />
          )}
          {step === 3 && bundle && option && selectedTier && (
            <ReviewStep
              bundle={bundle}
              tier={selectedTier}
              target={option}
              approver={userName}
              confirmed={confirmed}
              locked={locked}
              onConfirm={setConfirmed}
            />
          )}
          {step === 4 && (
            <DeployStep
              status={deploy}
              targetName={option?.name ?? ''}
              retrying={busy === 'approve'}
              onRetry={retry}
              onRestart={restart}
              onHistory={onShowHistory}
            />
          )}
        </div>

        {step < 4 && (
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

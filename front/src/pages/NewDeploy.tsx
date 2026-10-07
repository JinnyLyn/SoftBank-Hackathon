import { useEffect, useState } from 'react'
import { api } from '../api'
import UploadSection from '../components/UploadSection'
import AnalysisSection from '../components/AnalysisSection'
import TargetSection from '../components/TargetSection'
import PlanSection from '../components/PlanSection'
import ResultSection from '../components/ResultSection'
import ProgressPanel from '../components/ProgressPanel'
import RecentDeploys from '../components/RecentDeploys'
import type {
  Analysis,
  CostLine,
  DeployRecord,
  DeployStatus,
  PlanSummary,
  ScaleInput,
  StepKey,
  StepStatus,
  Target,
} from '../types'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const isRunning = (s: DeployStatus) => Object.values(s.steps).includes('active')

function now() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function NewDeploy({ onShowHistory }: { onShowHistory: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [scale, setScale] = useState<ScaleInput>({ expectedUsers: '~100', purpose: '' })
  const [analyzing, setAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [targets, setTargets] = useState<Target[]>(['aws', 'onprem'])
  const [costs, setCosts] = useState<CostLine[] | null>(null)
  const [plan, setPlan] = useState<PlanSummary | null>(null)
  const [approved, setApproved] = useState(false)
  const [deploy, setDeploy] = useState<DeployStatus | null>(null)
  const [runId, setRunId] = useState(0)
  const [records, setRecords] = useState<DeployRecord[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.history().then(setRecords).catch(() => {})
  }, [])

  // 분석이 끝났거나 배포 대상이 바뀌면 비용과 plan을 다시 받음
  useEffect(() => {
    if (!analysis || approved) return
    setCosts(null)
    setPlan(null)
    if (targets.length === 0) return
    let cancelled = false
    Promise.all([
      api.estimate(analysis.projectId, targets, scale),
      api.plan(analysis.projectId, targets),
    ])
      .then(([c, p]) => {
        if (cancelled) return
        setCosts(c)
        setPlan(p)
      })
      .catch((e) => !cancelled && setError(errMsg(e)))
    return () => {
      cancelled = true
    }
    // scale은 분석 이후 바꿀 수 없으므로 의존성에서 뺌
  }, [analysis, targets, approved])

  // 승인 후 상태 폴링
  useEffect(() => {
    if (!runId || !analysis || !file) return
    let stopped = false
    const tick = async () => {
      try {
        const s = await api.status(analysis.projectId)
        if (stopped) return
        setDeploy(s)
        if (isRunning(s)) {
          setTimeout(tick, 800)
          return
        }
        const ok = s.steps.health === 'done'
        const app = file.name.replace(/\.zip$/i, '')
        setRecords((prev) => {
          const version = 'v' + (prev.filter((r) => r.app === app).length + 1)
          const added: DeployRecord[] = targets.map((t) => ({
            id: `${runId}-${t}-${Date.now()}`,
            app,
            version,
            target: t,
            method: t === 'aws' ? 'Terraform' : 'Docker Compose',
            status: ok ? 'success' : 'failed',
            note: ok ? undefined : '헬스체크 실패',
            createdAt: now(),
          }))
          return [...added, ...prev]
        })
      } catch (e) {
        if (!stopped) setError(errMsg(e))
      }
    }
    tick()
    return () => {
      stopped = true
    }
  }, [runId])

  const chooseFile = (f: File) => {
    setFile(f)
    setAnalysis(null)
    setCosts(null)
    setPlan(null)
    setApproved(false)
    setDeploy(null)
    setError(null)
  }

  const runAnalyze = async () => {
    if (!file) return
    setAnalyzing(true)
    setError(null)
    try {
      setAnalysis(await api.analyze(file, scale))
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setAnalyzing(false)
    }
  }

  const toggleTarget = (t: Target) =>
    setTargets((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]))

  const startDeploy = async () => {
    if (!analysis) return
    setApproved(true)
    setError(null)
    try {
      await api.approve(analysis.projectId, targets)
      setRunId((n) => n + 1)
    } catch (e) {
      setApproved(false)
      setError(errMsg(e))
    }
  }

  const steps: Record<StepKey, StepStatus> = deploy?.steps ?? {
    upload: file ? 'done' : 'active',
    analyze: analyzing ? 'active' : analysis ? 'done' : 'waiting',
    approve: approved ? 'done' : analysis ? 'active' : 'waiting',
    build: approved ? 'active' : 'waiting',
    deploy: 'waiting',
    health: 'waiting',
  }

  return (
    <div className="layout">
      <div className="main-col">
        <div className="page-head">
          <h1>새 배포</h1>
          <p>zip을 올리면 AI가 분석하고 비용이 담긴 계획서를 만듭니다. 승인하면 AWS와 온프레미스에 배포합니다.</p>
        </div>

        {error && (
          <div className="error" role="alert">
            요청이 실패했습니다: {error}
          </div>
        )}

        <UploadSection
          file={file}
          scale={scale}
          analyzing={analyzing}
          analyzed={Boolean(analysis)}
          locked={approved}
          onFile={chooseFile}
          onScale={setScale}
          onAnalyze={runAnalyze}
        />
        <AnalysisSection analysis={analysis} loading={analyzing} />
        <TargetSection
          enabled={Boolean(analysis)}
          locked={approved}
          targets={targets}
          costs={costs}
          scale={scale}
          onToggle={toggleTarget}
        />
        <PlanSection
          key={targets.join(',')}
          plan={plan}
          enabled={Boolean(analysis)}
          approved={approved}
          onApprove={startDeploy}
        />
        {deploy && <ResultSection status={deploy} onRetry={startDeploy} />}
      </div>

      <aside className="side-col">
        <ProgressPanel steps={steps} urls={deploy?.urls ?? {}} />
        <RecentDeploys records={records} onShowAll={onShowHistory} />
      </aside>
    </div>
  )
}

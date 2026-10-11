import { useEffect, useRef, useState } from 'react'
import { analyze, status } from '../api/real'
import CodeView from '../components/CodeView'
import type { Analysis, DeployStatus, ScaleInput, Source } from '../types'
import { approveLocal, Operations, readiness, recommendLocal, type LocalReview, type Readiness } from './api'
import './onprem.css'

const names = { api: 'API', db: 'DB', docker: 'Docker', analyzer: '구성 분석기', worker: '로컬 배포 worker' }
const states = { ready: '준비됨', starting: '시작 중', down: '중단', stale: '상태 만료', unconfigured: '설정 필요' }
// Preserve the local planner's small default; cloud budgets are not a user decision here.
const LOCAL_SCALE: ScaleInput = { expectedUsers: '~100', pattern: 'steady', monthlyBudgetUsd: 0, purpose: '' }
// The shared analyzer describes AWS DB injection; this dedicated route uses local Compose env files.
const localFindingDetail = (finding: Analysis['findings'][number]) =>
  finding.title === 'DB 접속 정보는 배포가 넣습니다'
    ? '앱 전용 로컬 MySQL 컨테이너를 사용합니다. 배포할 때 만드는 로컬 secrets/app.env·secrets/db.env 파일로 DATABASE_URL과 DB 비밀번호를 컨테이너에 전달합니다.'
    : finding.detail
const message = (error: unknown) => error instanceof Error ? error.message : String(error)
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export default function OnPremDeploy() {
  const [health, setHealth] = useState<Readiness | null>(null)
  const [healthError, setHealthError] = useState('준비 상태를 확인하고 있습니다.')
  const [source, setSource] = useState<Source | null>(null)
  const [kind, setKind] = useState<'zip' | 'github'>('zip')
  const [url, setUrl] = useState('')
  const [branch, setBranch] = useState('main')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [review, setReview] = useState<LocalReview | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [deployment, setDeployment] = useState<string | null>(null)
  const [result, setResult] = useState<DeployStatus | null>(null)
  const [pollError, setPollError] = useState('')
  const operations = useRef(new Operations())
  const mockConfigured = import.meta.env.VITE_USE_MOCK === 'true'
  const ready = !mockConfigured && health?.status === 'ready'
  const readyRef = useRef(false)
  readyRef.current = ready

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    async function check() {
      try {
        const next = await readiness()
        if (alive) { setHealth(next); setHealthError('') }
      } catch (e) {
        if (alive) { setHealth(null); setHealthError(message(e)) }
      }
      if (alive) timer = setTimeout(check, 3000)
    }
    void check()
    return () => { alive = false; clearTimeout(timer); operations.current.invalidate() }
  }, [])

  useEffect(() => {
    if (!deployment) return
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    async function check() {
      try {
        const next = await status(deployment!)
        if (!alive) return
        setResult(next); setPollError('')
        if (next.state !== 'running') return
      } catch (e) { if (alive) setPollError(message(e)) }
      if (alive) timer = setTimeout(check, 3000)
    }
    void check()
    return () => { alive = false; clearTimeout(timer) }
  }, [deployment])

  async function loadPlan(projectId: string, current: () => boolean) {
    const until = Date.now() + 5 * 60 * 1000
    while (current()) {
      const next = await recommendLocal(projectId)
      if (!current()) return
      if (next) { setReview(next); return }
      if (Date.now() >= until) throw new Error('5분 동안 로컬 계획을 받지 못했습니다. 준비 상태를 확인한 뒤 계획을 다시 조회해 주세요.')
      await sleep(2000)
    }
  }
  function invalidate() { operations.current.invalidate(); setAnalysis(null); setReview(null); setConfirmed(false); setBusy(''); setError('') }
  async function start() {
    const selected = kind === 'zip' ? source : { kind: 'github' as const, url: url.trim(), branch: branch.trim() || 'main' }
    if (!selected || !readyRef.current || busy || deployment) return
    const current = operations.current.next()
    setReview(null); setAnalysis(null); setConfirmed(false); setError(''); setBusy('소스 업로드 및 구성 분석 중')
    try {
      const next = await analyze(selected, LOCAL_SCALE)
      if (!current()) return
      setAnalysis(next)
      if (next.blockers?.length || next.dockerfileDraft) return
      setBusy('로컬 Docker 계획 대기 중')
      await loadPlan(next.projectId, current)
    } catch (e) { if (current()) setError(message(e)) }
    finally { if (current()) setBusy('') }
  }
  async function refreshPlan() {
    if (!analysis || busy || deployment) return
    const current = operations.current.next()
    setReview(null); setConfirmed(false); setError(''); setBusy('로컬 계획 다시 조회 중')
    try { await loadPlan(analysis.projectId, current) }
    catch (e) { if (current()) setError(message(e)) }
    finally { if (current()) setBusy('') }
  }
  async function approve() {
    if (!review || !confirmed || !readyRef.current || busy || deployment) return
    const current = operations.current.next()
    const reviewed = review.reference
    setBusy('검토한 계획 승인 및 배포 등록 중'); setError('')
    try {
      await approveLocal(reviewed)
      if (current()) setDeployment(reviewed.projectId)
    } catch (e) { if (current()) { setError(message(e)); setConfirmed(false) } }
    finally { if (current()) setBusy('') }
  }
  const validSource = kind === 'zip' ? !!source : /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+(?:\.git)?\/?$/.test(url.trim())
  const costAllowed = review?.monthlyUsd === 0
  const locked = !!busy || !!deployment
  const httpUrl = result?.url && /^http:\/\//i.test(result.url) ? result.url : null

  return <div className="onprem">
    <header className="onprem-header"><div><strong>Paved Clouds</strong><span>로컬 Docker 배포</span></div></header>
    <main className="onprem-main">
      <section className="onprem-intro"><h1>이 플랫폼 PC에 앱 배포하기</h1>
        <p>플랫폼을 실행하는 PC의 Docker를 사용합니다. 방문자의 PC나 SSH 원격 서버에 배포하지 않습니다.</p>
        <p>HTTP 전용 · 독립 도메인·HTTPS 없음 · 전기·장비·네트워크 비용은 추정에서 제외됩니다.</p>
        <p>소스 분석은 규칙 기반이며 LLM을 호출하지 않습니다.</p>
      </section>
      <section className="onprem-panel" aria-labelledby="onprem-readiness">
        <h2 id="onprem-readiness">서비스 준비 상태 <span data-testid="readiness">{health ? states[health.status] : '연결 확인 불가'}</span></h2>
        {mockConfigured && <p role="alert">MOCK 설정에서는 로컬 배포를 실행하지 않습니다. 운영자에게 실제 API 실행 설정을 요청해 주세요.</p>}
        <ul className="onprem-components">{Object.entries(names).map(([key, name]) => {
          const component = health?.components[key as keyof typeof names]
          return <li key={key}><strong>{name}</strong><span>{component ? states[component.status] : '확인 불가'}{component?.phase ? ` (${component.phase})` : ''}</span></li>
        })}</ul>
        {!health && <p role="status">{healthError} 운영자에게 서비스 실행 상태 확인을 요청해 주세요.</p>}
        {health?.reasons.map((reason) => <p key={reason.code}>{reason.message} {reason.action}</p>)}
        {!ready && <p>새 분석·승인은 준비 후 가능합니다. 입력과 검토 중인 계획은 유지됩니다. 이미 승인한 대기 작업은 자동 취소되지 않습니다.</p>}
        <p className="hint">3초 간격으로 확인합니다. 분석·빌드 작업 중에는 새 작업이 대기열에서 기다릴 수 있습니다.</p>
      </section>
      <section className="onprem-panel" aria-labelledby="onprem-input"><h2 id="onprem-input">1. 앱 소스 선택</h2>
        <fieldset disabled={locked} className="onprem-fields">
          <div className="onprem-source-kind" role="group" aria-label="소스 방식">
            <button type="button" className="btn btn-ghost" data-testid="source-zip-choice" aria-pressed={kind === 'zip'} onClick={() => { if (kind !== 'zip') { invalidate(); setSource(null); setKind('zip') } }}>ZIP 파일</button>
            <button type="button" className="btn btn-ghost" data-testid="source-github" aria-pressed={kind === 'github'} onClick={() => { if (kind !== 'github') { invalidate(); setSource(null); setKind('github') } }}>GitHub 공개 저장소</button>
          </div>
          {kind === 'zip' ? <label>앱 ZIP 파일 (최대 200MB)<input data-testid="source-zip" type="file" accept=".zip" onChange={(e) => {
            invalidate(); const file = e.target.files?.[0]; setSource(null)
            if (file && (!file.name.toLowerCase().endsWith('.zip') || file.size > 200 * 1024 * 1024)) setError('200MB 이하의 ZIP 파일을 선택해 주세요.')
            else if (file) setSource({ kind: 'zip', file })
          }} /></label> : <><label>GitHub 저장소 URL<input data-testid="source-url" value={url} placeholder="https://github.com/owner/repo" onChange={(e) => { invalidate(); setUrl(e.target.value) }} /></label><label>브랜치<input value={branch} onChange={(e) => { invalidate(); setBranch(e.target.value) }} /></label></>}
        </fieldset>
        <p className="hint">자원은 ‘작게 시작’ 기본 구성과 이 PC의 Docker CPU·메모리 한도를 기준으로 계산합니다. 실제 자원 한도는 배포 승인 전에 확인할 수 있습니다.</p>
        <p className="hint">Dockerfile·의존성 파일·.env.example을 포함하고 실제 비밀값·.env·node_modules·.venv는 제외해 주세요. 지원하지 않는 구성은 분석 결과로 안내합니다.</p>
        <button className="btn" data-testid="analyze" disabled={!ready || locked || !validSource} onClick={() => void start()}>구성 분석 및 로컬 계획 만들기</button>
      </section>
      {busy && <p role="status" className="onprem-panel">{busy}</p>}
      {error && <p role="alert" className="onprem-error">{error}</p>}
      {analysis && <section className="onprem-panel"><h2>2. 구성 분석 결과</h2>
        <dl>{analysis.stack.map((row, i) => <div key={i}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
        {analysis.findings.map((finding, i) => <p key={i}><strong>{finding.title}</strong> {localFindingDetail(finding)}</p>)}
        {analysis.blockers?.map((blocker) => <p key={blocker} role="alert">{blocker}</p>)}
        {analysis.dockerfileDraft && <><p>Dockerfile 초안을 검토하여 소스에 추가한 뒤 다시 업로드해 주세요. 이 화면은 초안으로 자동 빌드하지 않습니다.</p><CodeView kind="hcl" text={analysis.dockerfileDraft.content} /></>}
        {!!analysis.evidence.length && <details><summary>분석 근거</summary><ul>{analysis.evidence.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}
        {!deployment && !analysis.blockers?.length && !analysis.dockerfileDraft && <button className="btn btn-ghost" disabled={!!busy} onClick={() => void refreshPlan()}>계획 다시 조회</button>}
      </section>}
      {review && <section className="onprem-panel" data-testid="review"><h2>3. Compose와 실행 변수 검토</h2>
        <p className="onprem-review-summary">{review.bundle.planInfo?.summary}</p>
        {costAllowed && <p>클라우드 호스팅 요금은 발생하지 않습니다. 이 PC의 전기·장비·네트워크 비용은 별도입니다.</p>}
        {review.bundle.planInfo?.excludedCosts?.length && <p>계획의 추가 제외 항목: {review.bundle.planInfo.excludedCosts.join(', ')}</p>}
        <dl className="onprem-identity">
          <div><dt>프로젝트</dt><dd>{review.reference.projectId}</dd></div>
          <div><dt>계획</dt><dd>{review.reference.planId}</dd></div>
          <div><dt>Fingerprint</dt><dd>{review.reference.fingerprint}</dd></div>
        </dl>
        {review.bundle.files.map((file) => <details key={file.name} open><summary>{file.name}</summary><CodeView kind="hcl" text={file.content} numbered /></details>)}
        <CodeView kind="plan" text={review.bundle.plan.text} />
        {!costAllowed && <p role="alert" data-testid="plan-cost-error">로컬 Docker 계획의 추가 호스팅 금액은 0이어야 합니다. 운영자에게 계획 확인을 요청해 주세요.</p>}
        {!review.bundle.planInfo?.ready && <p>실행 계획 파일이 아직 준비되지 않았습니다. 계획을 다시 조회해 주세요.</p>}
        <label className="onprem-consent"><input type="checkbox" data-testid="review-confirm" checked={confirmed} disabled={locked} onChange={(e) => setConfirmed(e.target.checked)} />이 PC의 Docker에서 위 파일과 변수로 빌드·배포하며 HTTP로만 접속함을 확인했습니다.</label>
        <button className="btn" data-testid="approve" disabled={!ready || !confirmed || locked || !costAllowed || !review.bundle.planInfo?.ready} onClick={() => void approve()}>검토한 계획 승인 및 배포</button>
      </section>}
      {deployment && <section className="onprem-panel" data-testid="deployment"><h2>4. 배포 진행 및 결과</h2>
        <p data-testid="deployment-state">{result?.state === 'success' ? '앱 HTTP 헬스 확인 완료' : result?.state === 'failed' ? '배포 실패' : '배포 진행 중'}</p>
        {pollError && <p role="alert">상태 조회 실패: {pollError} 기존 배포 상태를 다시 확인 중입니다.</p>}
        <CodeView kind="log" text={result?.log ?? ['배포 상태를 조회하고 있습니다.']} follow />
        {httpUrl && <p><a data-testid="app-url" href={httpUrl} target="_blank" rel="noreferrer">플랫폼 PC의 앱 HTTP 주소 열기</a><br />{httpUrl}<br />localhost/127.0.0.1 주소는 플랫폼 PC에서 여세요. 공개·원격 접속이나 HTTPS 성공을 의미하지 않습니다.</p>}
        {result?.state === 'failed' && <p>로그와 소스를 확인한 뒤 새 분석·계획·승인을 진행해 주세요. 자동 재배포·롤백은 하지 않습니다.</p>}
        {result && result.state !== 'running' && <button className="btn btn-ghost" data-testid="new-analysis" onClick={() => { invalidate(); setDeployment(null); setResult(null); setPollError('') }}>입력을 유지하고 새 분석 준비</button>}
      </section>}
    </main>
  </div>
}

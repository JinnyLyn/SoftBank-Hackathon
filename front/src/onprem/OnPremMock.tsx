import { useEffect, useState } from 'react'
import CodeView from '../components/CodeView'
import { LangSwitch, useT } from '../i18n'
import './onprem.css'

// This demo only stores source metadata. It never reads/uploads files or calls deployment APIs.
const COMPOSE = `# MOCK: example only
services:
  app:
    image: example/app:demo
    ports:
      - "8080:8080"
    cpus: 0.5
    mem_limit: 512m
`

export default function OnPremMock() {
  const t = useT()
  const [kind, setKind] = useState<'zip' | 'github'>('zip')
  const [filename, setFilename] = useState('')
  const [url, setUrl] = useState('')
  const [review, setReview] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const valid = kind === 'zip' ? !!filename : /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/?$/.test(url.trim())
  const running = step > 0 && step < 4
  const source = kind === 'zip' ? filename : url.trim()
  const logs = [t('MOCK: 예시 계획 승인 완료'), t('MOCK: 이미지 빌드 단계 시연'), t('MOCK: 컨테이너 시작 단계 시연'), t('MOCK: 배포 시연 완료 — 실제 앱은 배포되지 않았습니다.')]

  useEffect(() => {
    if (!running) return
    const timer = setTimeout(() => setStep((current) => current + 1), 700)
    return () => clearTimeout(timer)
  }, [step, running])

  function reset() { setReview(false); setConfirmed(false); setStep(0); setError('') }

  return <div className="onprem">
    <header className="onprem-header"><div><strong>Paved Clouds</strong>
      <button className="tab" onClick={() => window.location.assign('/')}>{t('Home')}</button>
      <span>{t('온프레미스 배포 · MOCK')}</span><LangSwitch className="onprem-lang" />
    </div></header>
    <main className="onprem-main">
      <section className="onprem-intro"><h1>{t('온프레미스 배포 체험하기')}</h1>
        <p data-testid="mock-notice">{t('MOCK: 파일 업로드·코드 분석·서버 배포 없이 화면 흐름만 시연합니다.')}</p>
        <p>{t('실제 온프레미스 배포는 대상 서버에 Paved Clouds 플랫폼과 Docker가 설치되어 있어야 합니다.')}</p>
        <p>{t('이 체험은 서버 설치나 연결 없이 진행할 수 있습니다. 선택한 파일과 저장소 주소는 서버로 전송하지 않습니다.')}</p>
      </section>
      <section className="onprem-panel"><h2>{t('1. 앱 소스 선택')}</h2>
        <fieldset className="onprem-fields" disabled={step > 0}>
          <div className="onprem-source-kind" role="group" aria-label={t('소스 방식')}>
            <button className="btn btn-ghost" data-testid="source-zip-choice" aria-pressed={kind === 'zip'} onClick={() => { if (kind !== 'zip') { reset(); setFilename(''); setKind('zip') } }}>{t('ZIP 파일')}</button>
            <button className="btn btn-ghost" data-testid="source-github" aria-pressed={kind === 'github'} onClick={() => { if (kind !== 'github') { reset(); setKind('github') } }}>{t('GitHub 공개 저장소')}</button>
          </div>
          {kind === 'zip' ? <label>{t('앱 ZIP 파일 (최대 200MB)')}<input data-testid="source-zip" type="file" accept=".zip" onChange={(e) => {
            reset(); setFilename('')
            const file = e.target.files?.[0]
            if (file && (!file.name.toLowerCase().endsWith('.zip') || file.size > 200 * 1024 * 1024)) setError('200MB 이하의 ZIP 파일을 선택해 주세요.')
            else if (file) setFilename(file.name)
          }} /></label> : <label>{t('GitHub 저장소 URL')}<input data-testid="source-url" value={url} placeholder="https://github.com/owner/repo" onChange={(e) => { reset(); setUrl(e.target.value) }} /></label>}
        </fieldset>
        {error && <p role="alert">{t(error)}</p>}
        <button className="btn" data-testid="analyze" disabled={!valid || step > 0} onClick={() => { if (valid && step === 0) { setReview(true); setConfirmed(false) } }}>{t('구성 분석 체험 (MOCK)')}</button>
      </section>
      {review && <section className="onprem-panel" data-testid="review"><h2>{t('2. 예시 구성 검토 · MOCK')}</h2>
        <dl><div><dt>{t('선택한 소스')}</dt><dd>{source}</dd></div></dl>
        <p>{t('아래 구성은 선택한 소스의 분석 결과가 아닌 고정 예시입니다.')}</p>
        <p>{t('예시 자원: 앱 컨테이너 1개 · CPU 0.5 · 메모리 512MB · HTTP 8080')}</p>
        <p>{t('실제 장비 용량과 호환성은 확인하지 않습니다. 전기·장비·네트워크 비용은 별도입니다.')}</p>
        <CodeView kind="hcl" text={COMPOSE} numbered />
        <label className="onprem-consent"><input type="checkbox" data-testid="review-confirm" checked={confirmed} disabled={step > 0} onChange={(e) => setConfirmed(e.target.checked)} />{t('예시 구성을 확인했으며 실제 배포가 아닌 시연임을 이해했습니다.')}</label>
        <button className="btn" data-testid="approve" disabled={!confirmed || step > 0} onClick={() => { if (confirmed && step === 0) setStep(1) }}>{t('승인하고 배포 체험 (MOCK)')}</button>
      </section>}
      {step > 0 && <section className="onprem-panel" data-testid="deployment"><h2>{t('3. 모의 배포 진행')}</h2>
        <p role="status" data-testid="deployment-state">{t(running ? 'MOCK: 배포 시연 중' : 'MOCK: 배포 시연 완료 — 실제 앱은 배포되지 않았습니다.')}</p>
        <CodeView kind="log" text={logs.slice(0, step)} follow />
        {!running && <><p>{t('실제 서버·컨테이너·접속 주소는 생성되지 않았습니다.')}</p>
          <button className="btn btn-ghost" data-testid="new-analysis" onClick={reset}>{t('입력을 유지하고 새 분석 준비')}</button></>}
      </section>}
    </main>
  </div>
}

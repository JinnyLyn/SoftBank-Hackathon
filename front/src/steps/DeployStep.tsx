import CodeView from '../components/CodeView'
import type { DeployStatus } from '../types'

interface Props {
  status: DeployStatus | null
  targetName: string
  retrying: boolean
  onRetry: () => void
  onRestart: () => void
  onHistory: () => void
}

export default function DeployStep({ status, targetName, retrying, onRetry, onRestart, onHistory }: Props) {
  if (!status) return <p className="muted">배포를 시작하는 중…</p>

  return (
    <div className="stack">
      {status.state === 'running' && (
        <div className="state-line">
          <span className="pulse" /> 이미지를 빌드하고 {targetName}에 올리는 중입니다. DB를 새로 만들면 몇 분 걸릴 수 있어요.
        </div>
      )}

      {status.state === 'success' && status.url && (
        <div className="done-card">
          <div>
            <span className="done-label">배포 완료</span>
            <a href={status.url} target="_blank" rel="noreferrer" className="mono">
              {status.url}
            </a>
          </div>
          <div className="done-actions">
            <button className="btn btn-ghost" onClick={onHistory}>
              이력 보기
            </button>
            <button className="btn" onClick={onRestart}>
              새 배포
            </button>
          </div>
        </div>
      )}

      {status.diagnosis && (
        <div className="diagnosis">
          <div className="diag-head">
            <span className="diag-label">배포 실패 · AI 진단</span>
          </div>
          <dl>
            <dt>원인</dt>
            <dd>{status.diagnosis.cause}</dd>
            <dt>수정안</dt>
            <dd>{status.diagnosis.fix}</dd>
          </dl>
          <div className="diff">
            <div className="diff-file mono">{status.diagnosis.patch.file}</div>
            {status.diagnosis.patch.before.map((l) => (
              <div key={'b' + l} className="diff-del mono">- {l}</div>
            ))}
            {status.diagnosis.patch.after.map((l) => (
              <div key={'a' + l} className="diff-add mono">+ {l}</div>
            ))}
          </div>
          <div className="diag-actions">
            <button className="btn btn-primary" onClick={onRetry} disabled={retrying}>
              {retrying ? '다시 배포하는 중…' : '수정 적용하고 다시 배포'}
            </button>
          </div>
        </div>
      )}

      <div className="code-box">
        <div className="code-tabs">
          <button className="is-on">배포 로그</button>
        </div>
        <CodeView kind="log" text={status.log} follow />
      </div>
    </div>
  )
}

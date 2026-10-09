import CodeView from '../components/CodeView'
import { IS_MOCK } from '../api'
import type { DeployStatus } from '../types'

interface Props {
  status: DeployStatus | null
  targetName: string
  fixing: boolean
  /** 수정안 반영 → 검증·plan 재생성 → 코드 검토에서 다시 승인 */
  onFix: () => void
  onRestart: () => void
  onHistory: () => void
}

export default function DeployStep({ status, targetName, fixing, onFix, onRestart, onHistory }: Props) {
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
            <span className="done-label">{IS_MOCK ? 'MOCK: 배포 완료 (예시, 실제로 배포되지 않음)' : '배포 완료'}</span>
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
            <span className="hint">수정안을 반영해 검증과 plan을 다시 만든 뒤, 코드 검토에서 다시 승인받습니다.</span>
            <button className="btn btn-primary" onClick={onFix} disabled={fixing}>
              {fixing ? '수정안 반영하는 중…' : '수정안 반영하고 다시 검토'}
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

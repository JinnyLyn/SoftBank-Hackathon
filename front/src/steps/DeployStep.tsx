import CodeView from '../components/CodeView'
import DomainProgress from '../components/DomainProgress'
import { IS_MOCK } from '../api'
import type { DeployStatus, DomainMode } from '../types'

/** 배포 상태를 확인하지 못한 경우. 배포 실패와는 다름 */
export interface PollIssue {
  kind: 'timeout' | 'error'
  message: string
}

interface Props {
  status: DeployStatus | null
  targetName: string
  /** 사용자가 고른 도메인 방식 */
  domainMode: DomainMode
  fixing: boolean
  pollIssue: PollIssue | null
  repolling: boolean
  /** 상태만 다시 읽음 (배포를 다시 요청하지 않음) */
  onRepoll: () => void
  /** 수정안 반영 → 검증·plan 재생성 → 코드 검토에서 다시 승인 */
  onFix: () => void
  onRestart: () => void
  onHistory: () => void
}

export default function DeployStep({
  status,
  targetName,
  domainMode,
  fixing,
  pollIssue,
  repolling,
  onRepoll,
  onFix,
  onRestart,
  onHistory,
}: Props) {
  const issue = pollIssue && (
    <div className="poll-issue" role="alert">
      <div>
        <strong>{pollIssue.kind === 'timeout' ? '배포 상태 확인이 늦어지고 있습니다' : '배포 상태를 확인하지 못했습니다'}</strong>
        <span>{pollIssue.message}</span>
        <span>
          배포가 실패했다는 뜻은 아닙니다. 중복 배포를 막기 위해 자동으로 다시 배포하지 않으니, 상태를 다시 조회하거나 배포
          이력에서 확인해 주세요.
        </span>
      </div>
      <div className="poll-actions">
        <button className="btn btn-ghost" onClick={onHistory}>
          이력 보기
        </button>
        <button className="btn btn-primary" onClick={onRepoll} disabled={repolling}>
          {repolling ? '조회 중…' : '상태 다시 조회'}
        </button>
      </div>
    </div>
  )

  if (!status) return issue || <p className="muted">배포를 시작하는 중…</p>

  return (
    <div className="stack">
      {issue}

      {status.state === 'running' && !pollIssue && (
        <div className="state-line">
          <span className="pulse" /> 이미지를 빌드하고 {targetName}에 올리는 중입니다. DB를 새로 만들면 몇 분 걸릴 수 있어요.
        </div>
      )}

      {status.state === 'success' && (
        <div className="done-card">
          <div>
            <span className="done-label">{IS_MOCK ? 'MOCK: 배포 완료 (예시, 실제로 배포되지 않음)' : '배포 완료'}</span>
            {status.url ? (
              <a href={status.url} target="_blank" rel="noreferrer" className="mono">
                {status.url}
              </a>
            ) : (
              <span className="muted small">접속 주소를 아직 받지 못했습니다.</span>
            )}
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

      {status.state === 'success' && status.domain && <DomainProgress status={status.domain} mode={domainMode} />}

      {status.state === 'failed' && (
        <div className="diagnosis">
          <div className="diag-head">
            <span className="diag-label">{status.diagnosis ? '배포 실패 · AI 진단' : '배포 실패'}</span>
          </div>
          {status.diagnosis ? (
            <>
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
            </>
          ) : (
            <p className="fail-note">원인 진단이 아직 없습니다. 아래 배포 로그를 확인하고 원인을 고친 뒤 새로 배포해 주세요.</p>
          )}
          <div className="diag-actions">
            <div className="done-actions">
              <button className="btn btn-ghost" onClick={onHistory}>
                이력 보기
              </button>
              <button className="btn" onClick={onRestart}>
                새 배포
              </button>
            </div>
            {status.diagnosis && (
              <button className="btn btn-primary" onClick={onFix} disabled={fixing}>
                {fixing ? '수정안 반영하는 중…' : '수정안 반영하고 다시 검토'}
              </button>
            )}
          </div>
        </div>
      )}

      <div className="code-box">
        <div className="code-tabs">
          <button className="is-on">배포 로그</button>
        </div>
        <CodeView kind="log" text={status.log.length ? status.log : ['(아직 로그가 없습니다)']} follow />
      </div>
    </div>
  )
}

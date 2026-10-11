import { useEffect, useState } from 'react'
import CodeView from '../components/CodeView'
import DomainProgress from '../components/DomainProgress'
import { IS_MOCK } from '../api'
import type { DeployStatus, DomainPlan } from '../types'
import { t as tr, useT } from '../i18n'

/** 배포 상태를 확인하지 못한 경우. 배포 실패와는 다름 */
export interface PollIssue {
  kind: 'timeout' | 'error'
  message: string
}

interface Props {
  status: DeployStatus | null
  targetName: string
  /** 사용자가 고른 도메인 방식 (none: 서버에 도메인 기능이 없어 미리보기 주소만) */
  domainMode: DomainPlan['mode']
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

// 10/11 실제 AWS 배포는 빌드부터 헬스체크까지 약 10분. 이보다 한참 길거나 같은 단계가 오래 이어지면 멈췄을 수 있다고 알림
const LONG_TOTAL_MS = 15 * 60_000
const LONG_STAGE_MS = 10 * 60_000

/** 분·초 표시 */
function clock(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(sec / 60)
  return m ? tr('{m}분 {s}초', { m, s: sec % 60 }) : tr('{s}초', { s: sec })
}

/**
 * 배포가 진행 중일 때 경과 시간과 마지막 로그 이후 시간. 상태 API에 시각이 없어 이 화면이 진행 중 상태를 처음 본 때부터 잰다
 * (승인 직후 이 화면으로 오므로 보통 배포 시작과 같음. 새로 고침하면 다시 0부터)
 */
function useElapsed(running: boolean, logCount: number) {
  const [start, setStart] = useState<number | null>(null)
  const [stage, setStage] = useState<{ count: number; at: number } | null>(null)
  const [now, setNow] = useState(() => Date.now())
  // 렌더 중 상태 갱신 (React 권장 방식: 이전 값과 비교해 바뀐 때만)
  if (running && start === null) setStart(Date.now())
  if (!running && start !== null) setStart(null)
  if (running && stage?.count !== logCount) setStage({ count: logCount, at: Date.now() })
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])
  if (!running || start === null) return null
  return { total: Math.max(0, now - start), stage: Math.max(0, now - (stage?.at ?? start)) }
}

/** ALB 기본 주소(*.elb.amazonaws.com)의 https. 공용 ALB 인증서는 팀 도메인용이라 이 이름과 맞지 않음 (10/11 실제 배포에서 확인) */
function isAwsDefaultHttps(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === 'https:' && u.hostname.endsWith('.elb.amazonaws.com')
  } catch {
    return false
  }
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
  const t = useT()
  const shownTarget = t(targetName)
  const issue = pollIssue && (
    <div className="poll-issue" role="alert">
      <div>
        <strong>{pollIssue.kind === 'timeout' ? t('배포 상태 확인이 늦어지고 있습니다') : t('배포 상태를 확인하지 못했습니다')}</strong>
        <span>{pollIssue.message}</span>
        <span>
          {t('배포가 실패했다는 뜻은 아닙니다. 중복 배포를 막기 위해 자동으로 다시 배포하지 않으니, 상태를 다시 조회하거나 배포 이력에서 확인해 주세요.')}
        </span>
      </div>
      <div className="poll-actions">
        <button className="btn btn-ghost" onClick={onHistory}>
          {t('이력 보기')}
        </button>
        <button className="btn btn-primary" onClick={onRepoll} disabled={repolling}>
          {repolling ? t('조회 중…') : t('상태 다시 조회')}
        </button>
      </div>
    </div>
  )

  const elapsed = useElapsed(status?.state === 'running', status?.log.length ?? 0)

  if (!status) return issue || <p className="muted">{t('배포를 시작하는 중…')}</p>
  const slow = elapsed && (elapsed.total > LONG_TOTAL_MS || elapsed.stage > LONG_STAGE_MS)

  return (
    <div className="stack">
      {issue}

      {status.state === 'running' && !pollIssue && (
        <div className="state-line">
          <span className="pulse" /> {t('이미지를 빌드하고 {target}에 올리는 중입니다. 보통 10분 안팎 걸려요.', { target: shownTarget })}
          {elapsed && <span className="muted small">{t('경과 {time}', { time: clock(elapsed.total) })}</span>}
        </div>
      )}

      {status.state === 'running' && !pollIssue && slow && elapsed && (
        <p className="hint" role="status">
          {elapsed.stage > LONG_STAGE_MS
            ? t('같은 단계가 {time}째 이어지고 있어요.', { time: clock(elapsed.stage) })
            : t('평소보다 오래 걸리고 있어요.')}{' '}
          {t('이미지를 받거나 올리는 중에 네트워크가 느리거나 끊기면 이렇게 될 수 있습니다. 작업에는 시간 한도가 있어서, 멈춘 경우 잠시 뒤 실패로 바뀌고 아래 로그에 이유가 남습니다. 그때 ‘새 배포’로 다시 시도해 주세요.')}
        </p>
      )}

      {status.state === 'success' && (
        <div className="done-card">
          <div>
            <span className="done-label">{IS_MOCK ? t('MOCK: 앱 배포 완료 (예시, 실제로 배포되지 않음)') : t('앱 배포 완료')}</span>
            {status.url ? (
              <a href={status.url} target="_blank" rel="noreferrer" className="mono">
                {status.url}
              </a>
            ) : (
              <span className="muted small">{t('접속 주소를 아직 받지 못했습니다.')}</span>
            )}
            {/* 독립 도메인이 연결되기 전 주소는 미리보기. 도메인 연결 성공으로 보이지 않게 (PRODUCT_DIRECTION §2) */}
            {status.url && status.domain?.state !== 'active' && (
              <span className="muted small">
                {domainMode === 'none' || !status.domain || status.domain.state === 'skipped'
                  ? t('미리보기 주소입니다. 도메인은 연결되지 않았습니다.')
                  : t('미리보기 주소입니다. 주소 연결은 아래에서 진행 중입니다.')}
              </span>
            )}
            {status.url && isAwsDefaultHttps(status.url) && (
              <span className="muted small">
                {t('AWS 기본 주소에는 이 주소용 HTTPS 인증서가 없어 브라우저가 ‘안전하지 않음’ 경고를 띄울 수 있습니다. 앱은 정상이며, 경고 없는 주소는 도메인 연결 뒤에 생깁니다.')}
              </span>
            )}
          </div>
          <div className="done-actions">
            <button className="btn btn-ghost" onClick={onHistory}>
              {t('이력 보기')}
            </button>
            <button className="btn" onClick={onRestart}>
              {t('새 배포')}
            </button>
          </div>
        </div>
      )}

      {status.state === 'success' && status.domain && <DomainProgress status={status.domain} mode={domainMode} />}

      {status.state === 'failed' && (
        <div className="diagnosis">
          <div className="diag-head">
            <span className="diag-label">{status.diagnosis ? t('배포 실패 · AI 진단') : t('배포 실패')}</span>
          </div>
          {status.diagnosis ? (
            <>
              <dl>
                <dt>{t('원인')}</dt>
                <dd>{status.diagnosis.cause}</dd>
                <dt>{t('수정안')}</dt>
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
            <p className="fail-note">{t('원인 진단이 아직 없습니다. 아래 배포 로그를 확인하고 원인을 고친 뒤 새로 배포해 주세요.')}</p>
          )}
          <div className="diag-actions">
            <div className="done-actions">
              <button className="btn btn-ghost" onClick={onHistory}>
                {t('이력 보기')}
              </button>
              <button className="btn" onClick={onRestart}>
                {t('새 배포')}
              </button>
            </div>
            {status.diagnosis && (
              <button className="btn btn-primary" onClick={onFix} disabled={fixing}>
                {fixing ? t('수정안 반영하는 중…') : t('수정안 반영하고 다시 검토')}
              </button>
            )}
          </div>
        </div>
      )}

      <div className="code-box">
        <div className="code-tabs">
          <button className="is-on">{t('배포 로그')}</button>
        </div>
        <CodeView kind="log" text={status.log.length ? status.log : [t('(아직 로그가 없습니다)')]} follow />
      </div>
    </div>
  )
}

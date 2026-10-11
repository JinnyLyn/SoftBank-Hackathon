import { useEffect, useState } from 'react'
import { subscribeAlerts, type AppAlert } from '../api/alerts'
import { useT } from '../i18n'

/** 사용률 보고가 이보다 오래되면 표시하지 않음(worker가 멈췄거나 앱이 내려감) */
const STALE_MS = 5 * 60 * 1000

/**
 * 화면 위 서버 사용률과 운영 알림. worker가 1분마다 보내는 실제 CPU 값을 SSE로 받아 보여 주고,
 * 자동 확장이 최대 대수까지 늘었는데도 80%를 넘으면 경고로 바꾼다.
 * 알림일 뿐 구성은 바뀌지 않는다: 서버를 늘리려면 더 큰 안으로 다시 계획하고 승인해야 한다.
 */
export default function AlertBar() {
  const t = useT()
  const [alerts, setAlerts] = useState<Record<string, AppAlert>>({})
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => subscribeAlerts((a) => setAlerts((m) => ({ ...m, [a.deploymentId]: a }))), [])
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30 * 1000)
    return () => window.clearInterval(timer)
  }, [])

  const fresh = Object.values(alerts).filter((a) => now - Date.parse(a.updatedAt) < STALE_MS || a.active)
  const hot = fresh.filter((a) => a.active && !hidden.has(a.deploymentId + a.updatedAt))
  const usage = fresh.filter((a) => !a.active && a.cpuPercent !== undefined)
  if (!hot.length && !usage.length) return null

  return (
    <>
      {hot.length > 0 && (
        <div className="alert-bar" role="status" aria-live="polite">
          {hot.map((a) => (
            <div key={a.deploymentId} className="alert-item">
              <span className="alert-dot" aria-hidden />
              <span>
                <strong>{a.app}</strong>{' '}
                {a.cpuPercent !== undefined && a.maxTasks !== undefined
                  ? t('서버 사용률이 최근 {m}분 평균 {cpu}%이고, 자동 확장이 최대 {max}대를 모두 쓰고 있습니다. 더 큰 안으로 다시 계획해 승인하면 서버를 늘릴 수 있습니다.', {
                      m: a.windowMinutes ?? 5,
                      cpu: Math.round(a.cpuPercent),
                      max: a.maxTasks,
                    })
                  : a.message}
              </span>
              <button type="button" className="link-btn" onClick={() => setHidden((s) => new Set(s).add(a.deploymentId + a.updatedAt))}>
                {t('닫기')}
              </button>
            </div>
          ))}
        </div>
      )}
      {usage.length > 0 && (
        <div className="usage-bar" aria-live="polite">
          <div className="usage-inner">
            <span className="usage-label">{t('서버 사용률 (실시간)')}</span>
            {usage.map((a) => (
              <span key={a.deploymentId} className="usage-chip">
                <span className="usage-dot" aria-hidden />
                {a.app} <strong>{Math.round(a.cpuPercent ?? 0)}%</strong>
                {a.runningTasks !== undefined && a.maxTasks !== undefined && (
                  <small>{t('실행 중 {running}/{max}대', { running: a.runningTasks, max: a.maxTasks })}</small>
                )}
              </span>
            ))}
            <span className="usage-note">{t('최대 대수를 다 쓰고 80%를 넘으면 알림')}</span>
          </div>
        </div>
      )}
    </>
  )
}

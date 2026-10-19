import { useEffect, useState } from 'react'
import { subscribeAlerts, type AppAlert } from '../api/alerts'
import { useT } from '../i18n'

/**
 * 화면 위 운영 알림 띠. 자동 확장이 최대 대수까지 늘었는데도 서버 사용률이 높은 앱을 알린다.
 * 알림일 뿐 구성은 바뀌지 않는다: 서버를 늘리려면 더 큰 안으로 다시 계획하고 승인해야 한다.
 */
export default function AlertBar() {
  const t = useT()
  const [alerts, setAlerts] = useState<Record<string, AppAlert>>({})
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  useEffect(
    () =>
      subscribeAlerts(
        (a) =>
          setAlerts((m) => {
            const next = { ...m }
            if (a.active) next[a.deploymentId] = a
            else delete next[a.deploymentId]
            return next
          }),
        // 연결을 새로 열면 켜진 알림 전체가 다시 오므로, 끊긴 사이에 풀린 알림이 남지 않게 비운다
        () => setAlerts({}),
      ),
    [],
  )

  const shown = Object.values(alerts).filter((a) => !hidden.has(a.deploymentId + a.updatedAt))
  if (!shown.length) return null
  return (
    <div className="alert-bar" role="status" aria-live="polite">
      {shown.map((a) => (
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
  )
}

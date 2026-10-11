/**
 * 배포된 앱의 운영 알림(지금은 서버 용량). 백엔드 GET /api/alerts/stream 을 SSE(EventSource)로 받는다.
 * 서버→브라우저 한 방향이라 WebSocket이 필요 없고, 연결이 끊기면 브라우저가 Last-Event-ID로 이어 받는다.
 * MOCK 모드에는 알림이 없다(구독하지 않음).
 */
export interface AppAlert {
  deploymentId: string
  app: string
  url: string | null
  kind: 'capacity'
  active: boolean
  cpuPercent?: number
  runningTasks?: number
  maxTasks?: number
  windowMinutes?: number
  /** worker가 만든 원문(한국어). 화면은 지표로 문장을 다시 만들어 번역한다 */
  message: string
  updatedAt: string
}

interface AlertOut {
  deployment_id: string
  app: string
  url: string | null
  kind: 'capacity'
  active: boolean
  message: string
  metrics: { cpu_percent: number; running_tasks: number; max_tasks: number; window_minutes: number } | null
  updated_at: string
}

const toAlert = (a: AlertOut): AppAlert => ({
  deploymentId: a.deployment_id,
  app: a.app,
  url: a.url,
  kind: a.kind,
  active: a.active,
  cpuPercent: a.metrics?.cpu_percent,
  runningTasks: a.metrics?.running_tasks,
  maxTasks: a.metrics?.max_tasks,
  windowMinutes: a.metrics?.window_minutes,
  message: a.message,
  updatedAt: a.updated_at,
})

/** 알림을 구독. 돌려준 함수로 끊는다. 이 기능이 없는 예전 백엔드(404)면 EventSource가 다시 시도하다 멈추게 몇 번 뒤 닫는다 */
export function subscribeAlerts(onAlert: (a: AppAlert) => void): () => void {
  if (import.meta.env.VITE_USE_MOCK === 'true' || typeof EventSource === 'undefined') return () => {}
  const source = new EventSource('/api/alerts/stream')
  let failures = 0
  source.addEventListener('alert', (e) => {
    failures = 0
    try {
      onAlert(toAlert(JSON.parse((e as MessageEvent<string>).data) as AlertOut))
    } catch {
      // 형식이 다른 메시지는 무시
    }
  })
  source.onopen = () => {
    failures = 0
  }
  source.onerror = () => {
    failures += 1
    if (failures >= 5) source.close()   // 서버에 알림 기능이 없거나 계속 끊기면 그만둔다(화면 동작에는 영향 없음)
  }
  return () => source.close()
}

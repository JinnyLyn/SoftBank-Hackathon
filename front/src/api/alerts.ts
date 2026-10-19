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

/**
 * 알림을 구독. 돌려준 함수로 끊는다.
 * 브라우저가 스스로 다시 연결하는 동안(readyState CONNECTING)에는 아무것도 하지 않고 Last-Event-ID로 이어 받게 둔다.
 * 응답을 거부당해 브라우저가 포기한 경우(CLOSED: 프록시 502·잘못된 Content-Type, 404 등)에는 점점 늘리는 간격(최대 60초)으로
 * 직접 다시 연다. 백엔드를 다시 배포하는 동안 오래 끊겨도 포기하지 않고 계속 시도한다.
 * 포기하는 경우는 하나뿐이다: 알림 API(/api/alerts)가 404·405인 알림 기능이 없는 예전 백엔드. EventSource는 HTTP 상태를
 * 알려주지 않아서, 포기하기 전에 fetch로 한 번 확인한다(화면 동작에는 영향 없음).
 * 직접 다시 연 연결은 Last-Event-ID가 없어 켜진 알림 전체를 다시 받으므로, 그 전에 onReset으로 화면의 알림을 비운다.
 */
const RETRY_FIRST_MS = 5_000
const RETRY_MAX_MS = 60_000

async function backendHasAlerts(): Promise<boolean> {
  try {
    const res = await fetch('/api/alerts?active_only=true', { cache: 'no-store' })
    return res.status !== 404 && res.status !== 405
  } catch {
    return true   // 연결이 안 되는 것이지 기능이 없는 것이 아니다: 계속 시도한다
  }
}

export function subscribeAlerts(onAlert: (a: AppAlert) => void, onReset?: () => void): () => void {
  if (import.meta.env.VITE_USE_MOCK === 'true' || typeof EventSource === 'undefined') return () => {}
  let source: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let stopped = false
  let delay = RETRY_FIRST_MS
  let reopened = false

  const connect = () => {
    const es = new EventSource('/api/alerts/stream')
    source = es
    es.addEventListener('alert', (e) => {
      try {
        onAlert(toAlert(JSON.parse((e as MessageEvent<string>).data) as AlertOut))
      } catch {
        // 형식이 다른 메시지는 무시
      }
    })
    es.onopen = () => {
      delay = RETRY_FIRST_MS
      if (reopened) onReset?.()
      reopened = false
    }
    es.onerror = () => {
      if (stopped || es.readyState !== EventSource.CLOSED) return
      es.close()
      void backendHasAlerts().then((supported) => {
        if (stopped || !supported) return
        reopened = true
        timer = setTimeout(connect, delay)
        delay = Math.min(delay * 2, RETRY_MAX_MS)
      })
    }
  }
  connect()

  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    source?.close()
  }
}

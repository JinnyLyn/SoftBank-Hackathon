import { useEffect, useState } from 'react'

export interface WorkStage {
  label: string
  state: 'done' | 'current' | 'todo' | 'failed'
}

interface Props {
  title: string
  /** 이 작업을 시작한 시각(ms). 경과 시간 표시용 */
  startedAt: number
  stages: WorkStage[]
  /** 지금 단계에서 실제로 하는 일. 몇 초마다 돌아가며 보여 줌 (하지 않는 일을 적지 않음) */
  hints: string[]
  /** 보통 걸리는 시간 같은 안내 */
  note?: string
}

const HINT_MS = 4000

/** 오래 걸리는 작업(분석, 계획)의 진행 표시. 단계는 서버가 알려 준 실제 신호로만 움직임 */
export default function WorkProgress({ title, startedAt, stages, hints, note }: Props) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  const failed = stages.some((s) => s.state === 'failed')
  const hint = hints.length ? hints[Math.floor((now - startedAt) / HINT_MS) % hints.length] : ''

  return (
    <div className="work-progress" aria-live="polite">
      <div className="work-head">
        <strong>
          {!failed && <span className="pulse" aria-hidden />} {title}
        </strong>
        <span className="muted small mono">{elapsed < 60 ? `${elapsed}초` : `${Math.floor(elapsed / 60)}분 ${elapsed % 60}초`}</span>
      </div>
      <ol className="work-steps">
        {stages.map((s) => (
          <li key={s.label} className={'is-' + s.state}>
            <span className="dot" aria-hidden />
            {s.label}
          </li>
        ))}
      </ol>
      {!failed && hint && (
        <p key={hint} className="work-hint">
          {hint}
        </p>
      )}
      {note && <p className="hint">{note}</p>}
    </div>
  )
}

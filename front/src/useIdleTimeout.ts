import { useEffect, useRef } from 'react'

const EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const

/** 일정 시간 동안 아무 조작이 없으면 onIdle 호출. enabled가 false면 타이머를 걸지 않음 */
export function useIdleTimeout(ms: number, onIdle: () => void, enabled: boolean) {
  const cb = useRef(onIdle)
  cb.current = onIdle

  useEffect(() => {
    if (!enabled) return
    let timer = window.setTimeout(() => cb.current(), ms)
    const reset = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => cb.current(), ms)
    }
    EVENTS.forEach((e) => window.addEventListener(e, reset, { passive: true }))
    return () => {
      window.clearTimeout(timer)
      EVENTS.forEach((e) => window.removeEventListener(e, reset))
    }
  }, [ms, enabled])
}

import type { Source, Tier } from './types'

/** 소스에서 앱 이름을 뽑음 (zip 파일 이름, 저장소 이름) */
export const sourceName = (s: Source) =>
  s.kind === 'zip'
    ? s.file.name.replace(/\.zip$/i, '')
    : s.url.replace(/\/+$/, '').replace(/\.git$/, '').split('/').pop() || 'app'

export const usd = (n: number) => '$' + n.toFixed(2)

export const tierTotal = (t: Tier) => t.resources.reduce((s, r) => s + r.monthlyUsd, 0)

/** 비용이 0이면(온프레미스) 금액 대신 문구 */
export const costText = (n: number) => (n > 0 ? usd(n) : '추가 비용 없음')

/**
 * 화면용 짧은 시각 "MM-DD HH:mm". 백엔드는 UTC ISO 8601, mock은 "YYYY-MM-DD HH:mm"을 주므로 둘 다 받음
 */
export function shortTime(value: string) {
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value)) return value.slice(5)
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function now() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

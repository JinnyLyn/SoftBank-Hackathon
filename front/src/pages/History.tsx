import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import ProviderMark from '../components/ProviderMark'
import LoadError from '../components/LoadError'
import { costText, shortTime, usd } from '../format'
import type { DeployRecord } from '../types'

const STATUS_TEXT: Record<DeployRecord['status'], string> = {
  success: '성공',
  failed: '실패',
  running: '진행 중',
}

export default function History() {
  const [records, setRecords] = useState<DeployRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 조회 실패를 빈 이력으로 보이지 않게 따로 표시
  const load = useCallback(() => {
    setError(null)
    setRecords(null)
    api
      .history()
      .then(setRecords)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(load, [load])

  // 앱별로 묶어서 최신 배포가 위로
  const groups = useMemo(() => {
    const map = new Map<string, DeployRecord[]>()
    for (const r of records ?? []) map.set(r.app, [...(map.get(r.app) ?? []), r])
    return [...map.entries()]
  }, [records])

  const monthly = groups.reduce((s, [, rs]) => {
    const live = rs.find((r) => r.status === 'success')
    return s + (live?.monthlyUsd ?? 0)
  }, 0)

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>배포 이력</h1>
          <p>
            앱별 배포 기록입니다.
            {records && !error && ` 지금 떠 있는 앱 기준 월 예상 비용은 ${costText(monthly)}입니다.`}
          </p>
        </div>
      </div>

      {error && <LoadError what="배포 이력" message={error} onRetry={load} />}
      {!error && records === null && <p className="muted">불러오는 중…</p>}
      {records !== null && groups.length === 0 && <p className="muted">아직 배포한 앱이 없습니다.</p>}

      {groups.map(([app, rs]) => {
        const live = rs.find((r) => r.status === 'success')
        return (
          <section key={app} className="app-block">
            <header>
              <div>
                <h2>{app}</h2>
                {live?.url && (
                  <a href={live.url} target="_blank" rel="noreferrer" className="mono small">
                    {live.url}
                  </a>
                )}
              </div>
              {live && (
                <span className="app-cost">
                  {live.tier} · {live.monthlyUsd > 0 ? `${usd(live.monthlyUsd)}/월` : '추가 비용 없음'}
                </span>
              )}
            </header>
            <ol className="timeline">
              {rs.map((r) => (
                <li key={r.id} className={'is-' + r.status}>
                  <span className="t-ver">{r.version}</span>
                  <span className="t-main">
                    <span className="with-mark">
                      <ProviderMark provider={r.provider} /> {r.target} · {r.tier} 구성
                    </span>
                    {r.note && <small>{r.note}</small>}
                  </span>
                  <span className="t-time mono">{shortTime(r.createdAt)}</span>
                  <span className={'t-status rs-' + r.status}>{STATUS_TEXT[r.status]}</span>
                </li>
              ))}
            </ol>
          </section>
        )
      })}
    </div>
  )
}

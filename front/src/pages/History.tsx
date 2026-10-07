import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { STATUS_TEXT, targetName } from '../components/RecentDeploys'
import type { DeployRecord, Target } from '../types'

type Filter = 'all' | Target

export default function History() {
  const [records, setRecords] = useState<DeployRecord[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')

  useEffect(() => {
    api.history().then(setRecords).catch(() => setRecords([]))
  }, [])

  const rows = useMemo(
    () => (records ?? []).filter((r) => filter === 'all' || r.target === filter),
    [records, filter],
  )

  return (
    <div className="single">
      <div className="page-head page-head-row">
        <div>
          <h1>배포 이력</h1>
          <p>앱별로 언제, 어디에, 어떤 방식으로 배포했는지 기록합니다.</p>
        </div>
        <div className="segmented">
          {(
            [
              ['all', '전체'],
              ['aws', 'AWS'],
              ['onprem', '온프레미스'],
            ] as [Filter, string][]
          ).map(([k, label]) => (
            <button key={k} className={filter === k ? 'is-on' : ''} onClick={() => setFilter(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <section className="card">
        <table className="table">
          <thead>
            <tr>
              <th>앱</th>
              <th>대상</th>
              <th>방식</th>
              <th>시각</th>
              <th>비고</th>
              <th className="num">결과</th>
            </tr>
          </thead>
          <tbody>
            {records === null && (
              <tr>
                <td colSpan={6} className="empty">불러오는 중…</td>
              </tr>
            )}
            {records !== null && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">기록이 없습니다.</td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  <strong>{r.app}</strong> <span className="muted">{r.version}</span>
                </td>
                <td>{targetName(r)}</td>
                <td className="muted">{r.method}</td>
                <td className="muted mono">{r.createdAt}</td>
                <td className="muted">{r.note ?? '-'}</td>
                <td className="num">
                  <em className={'rs-' + r.status}>{STATUS_TEXT[r.status]}</em>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}

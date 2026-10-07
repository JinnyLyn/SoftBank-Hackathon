import type { DeployRecord } from '../types'

export const STATUS_TEXT: Record<DeployRecord['status'], string> = {
  success: '성공',
  failed: '실패',
  running: '진행 중',
}

export const targetName = (r: DeployRecord) => (r.target === 'aws' ? 'AWS' : '온프레미스')

export default function RecentDeploys({ records, onShowAll }: { records: DeployRecord[]; onShowAll: () => void }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2 className="panel-title">최근 배포 이력</h2>
        <button className="link-btn" onClick={onShowAll}>
          전체 보기
        </button>
      </div>
      <ul className="recent">
        {records.slice(0, 3).map((r) => (
          <li key={r.id}>
            <div>
              <strong>
                {r.app} {r.version}
              </strong>
              <span>
                {targetName(r)}, {r.note ?? r.method}
              </span>
            </div>
            <em className={'rs-' + r.status}>
              {STATUS_TEXT[r.status]}
              {r.status === 'failed' && r.note?.includes('롤백') ? ', 롤백' : ''}
            </em>
          </li>
        ))}
        {records.length === 0 && <li className="muted">아직 배포한 기록이 없습니다.</li>}
      </ul>
    </div>
  )
}

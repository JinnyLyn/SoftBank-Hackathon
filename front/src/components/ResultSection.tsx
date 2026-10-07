import Section from './Section'
import type { DeployStatus } from '../types'

interface Props {
  status: DeployStatus
  onRetry: () => void
}

export default function ResultSection({ status, onRetry }: Props) {
  const { diagnosis, urls, steps } = status
  const running = Object.values(steps).includes('active')

  return (
    <Section no={5} title="배포 결과">
      {running && <p className="placeholder">배포가 진행 중입니다. 오른쪽에서 단계별 상태를 볼 수 있어요.</p>}

      {diagnosis && (
        <div className="diagnosis">
          <div className="diag-block">
            <h3>원인</h3>
            <p>{diagnosis.cause}</p>
          </div>
          <div className="diag-block">
            <h3>수정 방향</h3>
            <p>{diagnosis.fix}</p>
          </div>
          <details>
            <summary>관련 로그</summary>
            <pre className="plan">{diagnosis.log}</pre>
          </details>
          <div className="approve-row">
            <span className="hint">수정안을 적용한 뒤 같은 plan으로 다시 배포합니다.</span>
            <button className="btn btn-primary" onClick={onRetry}>
              수정 적용 후 재배포
            </button>
          </div>
        </div>
      )}

      {!running && !diagnosis && (
        <ul className="url-list">
          {urls.aws && (
            <li>
              <span>AWS</span>
              <a href={urls.aws} target="_blank" rel="noreferrer">{urls.aws}</a>
            </li>
          )}
          {urls.onprem && (
            <li>
              <span>온프레미스</span>
              <a href={urls.onprem} target="_blank" rel="noreferrer">{urls.onprem}</a>
            </li>
          )}
        </ul>
      )}
    </Section>
  )
}

import type { StepKey, StepStatus, Target } from '../types'

const STEPS: { key: StepKey; label: string }[] = [
  { key: 'upload', label: 'zip 업로드' },
  { key: 'analyze', label: 'AI 분석, 비용 추정' },
  { key: 'approve', label: '사용자 승인' },
  { key: 'build', label: '이미지 빌드, 푸시' },
  { key: 'deploy', label: 'AWS, 온프레미스 배포' },
  { key: 'health', label: '헬스체크' },
]

function statusLabel(key: StepKey, s: StepStatus) {
  if (s === 'done') return '완료'
  if (s === 'failed') return '실패'
  if (s === 'active') return key === 'approve' || key === 'upload' ? '대기 중' : '진행 중'
  return '대기'
}

interface Props {
  steps: Record<StepKey, StepStatus>
  urls: Partial<Record<Target, string>>
}

export default function ProgressPanel({ steps, urls }: Props) {
  const hasUrl = Boolean(urls.aws || urls.onprem)

  return (
    <div className="panel">
      <h2 className="panel-title">배포 진행</h2>
      <ol className="steps">
        {STEPS.map((s) => {
          const st = steps[s.key]
          return (
            <li key={s.key} className={'st-' + st}>
              <span>{s.label}</span>
              <em>{statusLabel(s.key, st)}</em>
            </li>
          )
        })}
      </ol>
      <div className="url-box">
        <strong>접속 URL</strong>
        {hasUrl ? (
          <>
            {urls.aws && <a href={urls.aws} target="_blank" rel="noreferrer">{urls.aws}</a>}
            {urls.onprem && <a href={urls.onprem} target="_blank" rel="noreferrer">{urls.onprem}</a>}
          </>
        ) : (
          <span>배포가 끝나면 AWS, 온프레미스 URL이 여기에 표시됩니다.</span>
        )}
      </div>
    </div>
  )
}

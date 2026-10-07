import Section from './Section'
import type { Analysis } from '../types'

export default function AnalysisSection({ analysis, loading }: { analysis: Analysis | null; loading: boolean }) {
  if (!analysis) {
    return (
      <Section no={2} title="AI 분석 결과" muted={!loading}>
        <p className="placeholder">
          {loading ? '프레임워크, 포트, DB 사용 여부를 확인하고 있습니다…' : 'zip을 올리고 분석을 시작하면 여기에 표시됩니다.'}
        </p>
      </Section>
    )
  }

  const facts = [
    ['프레임워크', analysis.framework],
    ['컨테이너 포트', String(analysis.port)],
    ['DB', analysis.db],
    ['파일 저장', analysis.fileStorage],
  ]

  return (
    <Section no={2} title="AI 분석 결과">
      <dl className="facts">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>

      {analysis.notice && (
        <div className="notice">
          <strong>{analysis.notice.title}</strong>
          <span>{analysis.notice.detail}</span>
        </div>
      )}

      <div className="evidence">
        <h3>판단 근거 (스캐너가 찾은 증거)</h3>
        <ul>
          {analysis.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      </div>
    </Section>
  )
}

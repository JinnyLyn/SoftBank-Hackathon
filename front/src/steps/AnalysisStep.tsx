import ProviderMark from '../components/ProviderMark'
import { PROVIDERS } from '../providers'
import { costText, tierTotal, usd } from '../format'
import type { Analysis, Choice, Recommendation, TierKey } from '../types'

export type CodeState = 'idle' | 'loading' | 'ready' | 'error'

const CODE_TEXT: Record<CodeState, string> = {
  idle: '',
  loading: '배포 코드 만드는 중',
  ready: '배포 코드 준비됨',
  error: '배포 코드 생성 실패',
}

interface Props {
  analysis: Analysis
  rec: Recommendation
  choice: Choice
  codeState: CodeState
  codeError?: string
  locked: boolean
  onChoice: (c: Choice) => void
}

const TIER_KEYS: TierKey[] = ['lean', 'balanced', 'roomy']

export default function AnalysisStep({ analysis, rec, choice, codeState, codeError, locked, onChoice }: Props) {
  const option = rec.options.find((o) => o.connectionId === choice.connectionId)
  const selected = option?.tiers.find((t) => t.key === choice.tier)
  const isRec = (id: string, t: TierKey) => rec.recommended.connectionId === id && rec.recommended.tier === t

  // 열(구성 크기)마다 가장 싼 대상
  const cheapest = Object.fromEntries(
    TIER_KEYS.map((k) => {
      const costs = rec.options.map((o) => ({ id: o.connectionId, c: tierTotal(o.tiers.find((t) => t.key === k)!) }))
      return [k, costs.sort((a, b) => a.c - b.c)[0]?.id]
    }),
  ) as Record<TierKey, string | undefined>

  const head = rec.options[0]?.tiers

  return (
    <div className="stack-lg">
      <section>
        <h3 className="sub-title">코드에서 찾은 것</h3>
        <dl className="kv">
          {analysis.stack.map((s) => (
            <div key={s.label}>
              <dt>{s.label}</dt>
              <dd>{s.value}</dd>
            </div>
          ))}
        </dl>
        <ul className="findings">
          {analysis.findings.map((f) => (
            <li key={f.title} className={'finding is-' + f.level}>
              <strong>{f.title}</strong>
              <span>{f.detail}</span>
            </li>
          ))}
        </ul>
        <details className="evidence">
          <summary>판단 근거 {analysis.evidence.length}개</summary>
          <ul>
            {analysis.evidence.map((e) => (
              <li key={e} className="mono">{e}</li>
            ))}
          </ul>
        </details>
      </section>

      <section>
        <h3 className="sub-title">어디에, 어떤 크기로</h3>
        <p className="rec-reason">{rec.reason}</p>

        {rec.options.length === 0 ? (
          <p className="muted">비교할 배포 대상이 없습니다. 배포 대상 탭에서 하나 이상 연결해 주세요.</p>
        ) : (
          <div className="matrix-wrap">
            <table className="matrix">
              <thead>
                <tr>
                  <th>배포 대상</th>
                  {head?.map((t) => (
                    <th key={t.key}>
                      {t.label}
                      <small>{t.fit}</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rec.options.map((o) => (
                  <tr key={o.connectionId}>
                    <th scope="row">
                      <ProviderMark provider={o.provider} />
                      <span>
                        {o.name}
                        <small>{PROVIDERS[o.provider].label}</small>
                      </span>
                    </th>
                    {o.tiers.map((t) => {
                      const on = choice.connectionId === o.connectionId && choice.tier === t.key
                      return (
                        <td key={t.key}>
                          <button
                            className={'cell' + (on ? ' is-on' : '')}
                            disabled={locked}
                            aria-pressed={on}
                            onClick={() => onChoice({ connectionId: o.connectionId, tier: t.key })}
                          >
                            <span className="cell-tags">
                              {isRec(o.connectionId, t.key) && <span className="badge">AI 추천</span>}
                              {cheapest[t.key] === o.connectionId && rec.options.length > 1 && (
                                <span className="tag-low">최저</span>
                              )}
                            </span>
                            <strong>{costText(tierTotal(t))}</strong>
                            <small>{t.headline}</small>
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {option && selected && (
          <div className="tier-detail">
            <h4>
              <ProviderMark provider={option.provider} /> {option.name} · {selected.label} — {selected.headline}
              {codeState !== 'idle' && (
                <span className={'code-state is-' + codeState} title={codeError}>
                  {CODE_TEXT[codeState]}
                </span>
              )}
            </h4>
            <table className="table">
              <thead>
                <tr>
                  <th>리소스</th>
                  <th>사양</th>
                  <th>이렇게 고른 이유</th>
                  <th className="num">월</th>
                </tr>
              </thead>
              <tbody>
                {selected.resources.map((r) => (
                  <tr key={r.service}>
                    <td className="nowrap">
                      <strong>{r.service}</strong>
                    </td>
                    <td className="mono small">{r.spec}</td>
                    <td className="muted">{r.why}</td>
                    <td className="num">{r.monthlyUsd > 0 ? usd(r.monthlyUsd) : '-'}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>합계{selected.usageNote && <span className="muted"> · 서버 자원 {selected.usageNote}</span>}</td>
                  <td className="num">{costText(tierTotal(selected))}</td>
                </tr>
              </tfoot>
            </table>
            <p className="tradeoff">
              <span>감수할 점</span> {selected.tradeoff}
            </p>
            <ul className="assumptions">
              {rec.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  )
}

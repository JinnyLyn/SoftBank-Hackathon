import Section from './Section'
import type { CostLine, ScaleInput, Target } from '../types'

const TARGETS: { key: Target; name: string; desc: string }[] = [
  { key: 'aws', name: 'AWS', desc: 'ECS Fargate, Terraform 보유' },
  { key: 'onprem', name: '온프레미스', desc: 'Docker Compose, SSH 배포' },
]

interface Props {
  enabled: boolean
  locked: boolean
  targets: Target[]
  costs: CostLine[] | null
  scale: ScaleInput
  onToggle: (t: Target) => void
}

const usd = (n: number) => '$' + n.toFixed(2)

export default function TargetSection({ enabled, locked, targets, costs, scale, onToggle }: Props) {
  const total = costs?.reduce((s, c) => s + (c.monthlyUsd ?? 0), 0) ?? 0

  return (
    <Section no={3} title="배포 대상과 비용 추정" muted={!enabled}>
      <div className="targets">
        {TARGETS.map((t) => {
          const on = targets.includes(t.key)
          return (
            <label key={t.key} className={'target' + (on ? ' is-on' : '')}>
              <input
                type="checkbox"
                checked={on}
                disabled={!enabled || locked}
                onChange={() => onToggle(t.key)}
              />
              <span className="check" aria-hidden />
              <span>
                <strong>{t.name}</strong>
                <small>{t.desc}</small>
              </span>
            </label>
          )
        })}
      </div>

      <table className="table cost-table">
        <thead>
          <tr>
            <th>리소스</th>
            <th>구성</th>
            <th className="num">월 예상 비용</th>
          </tr>
        </thead>
        <tbody>
          {!costs || costs.length === 0 ? (
            <tr>
              <td colSpan={3} className="empty">
                {enabled && targets.length === 0 ? '배포 대상을 하나 이상 선택하세요.' : '[계산 후 표시]'}
              </td>
            </tr>
          ) : (
            costs.map((c) => (
              <tr key={c.target + c.resource}>
                <td>
                  {c.resource}
                  {targets.length > 1 && <span className="tag">{c.target === 'aws' ? 'AWS' : '온프레미스'}</span>}
                </td>
                <td className="muted">{c.spec}</td>
                <td className="num">{c.monthlyUsd === null ? <span className="muted">추가 비용 없음</span> : usd(c.monthlyUsd)}</td>
              </tr>
            ))
          )}
        </tbody>
        {costs && costs.length > 0 && (
          <tfoot>
            <tr>
              <td colSpan={2}>
                월 합계
                <span className="muted"> · 월 사용자 {scale.expectedUsers}명 기준, 서울 리전 온디맨드 가격</span>
              </td>
              <td className="num">{usd(total)}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </Section>
  )
}

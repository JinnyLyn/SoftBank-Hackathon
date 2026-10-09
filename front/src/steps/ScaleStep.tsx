import ProviderMark from '../components/ProviderMark'
import type { Connection, ExpectedUsers, ScaleInput, TrafficPattern } from '../types'

const USERS: { value: ExpectedUsers; note: string }[] = [
  { value: '~100', note: '시연, 지인 테스트' },
  { value: '~1,000', note: '동아리, 초기 서비스' },
  { value: '~10,000', note: '입소문이 난 서비스' },
  { value: '10,000+', note: '본격 운영' },
]

const PATTERNS: { value: TrafficPattern; label: string }[] = [
  { value: 'steady', label: '하루 종일 고르게' },
  { value: 'peak', label: '특정 시간에 몰림' },
  { value: 'unknown', label: '아직 모름' },
]

const BUDGETS = [10, 30, 50, 100]

export const budgetValid = (n: number) => Number.isFinite(n) && n >= 1

interface Props {
  scale: ScaleInput
  locked: boolean
  connections: Connection[]
  onChange: (s: ScaleInput) => void
  onShowConnections: () => void
}

export default function ScaleStep({ scale, locked, connections, onChange, onShowConnections }: Props) {
  const usable = connections.filter((c) => c.status === 'connected')
  const skipped = connections.length - usable.length

  return (
    <div className="stack">
      <fieldset className="field" disabled={locked}>
        <legend>한 달에 쓰는 사람 수</legend>
        <div className="choice-grid">
          {USERS.map((u) => (
            <label key={u.value} className={'choice' + (scale.expectedUsers === u.value ? ' is-on' : '')}>
              <input
                type="radio"
                name="users"
                checked={scale.expectedUsers === u.value}
                onChange={() => onChange({ ...scale, expectedUsers: u.value })}
              />
              <strong>{u.value}명</strong>
              <small>{u.note}</small>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="field" disabled={locked}>
        <legend>접속 패턴</legend>
        <div className="switch">
          {PATTERNS.map((p) => (
            <button
              key={p.value}
              type="button"
              className={scale.pattern === p.value ? 'is-on' : ''}
              onClick={() => onChange({ ...scale, pattern: p.value })}
            >
              {p.label}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="field">
        <label htmlFor="budget">월 예산 한도 (USD)</label>
        <div className="budget-row">
          <div className="budget-input">
            <span aria-hidden>$</span>
            <input
              id="budget"
              type="number"
              min={1}
              step={1}
              inputMode="numeric"
              className="input mono"
              disabled={locked}
              value={Number.isFinite(scale.monthlyBudgetUsd) ? scale.monthlyBudgetUsd : ''}
              onChange={(e) =>
                onChange({ ...scale, monthlyBudgetUsd: e.target.value === '' ? NaN : Number(e.target.value) })
              }
            />
          </div>
          <div className="switch">
            {BUDGETS.map((b) => (
              <button
                key={b}
                type="button"
                disabled={locked}
                className={scale.monthlyBudgetUsd === b ? 'is-on' : ''}
                onClick={() => onChange({ ...scale, monthlyBudgetUsd: b })}
              >
                ${b}
              </button>
            ))}
          </div>
        </div>
        <p className={budgetValid(scale.monthlyBudgetUsd) ? 'hint' : 'conn-error'}>
          {budgetValid(scale.monthlyBudgetUsd)
            ? '이 금액을 넘는 구성은 추천하지 않고 고를 수도 없습니다. 사내 서버는 추가 비용 0으로 계산합니다.'
            : '1 이상의 금액을 넣어 주세요.'}
        </p>
      </div>

      <div className="field">
        <label htmlFor="purpose">어떤 서비스인가요? (선택)</label>
        <textarea
          id="purpose"
          className="input"
          rows={3}
          disabled={locked}
          placeholder="예: 동아리 출석 체크. 평일 저녁 7시 모임 직전에 50명 정도가 한꺼번에 접속함"
          value={scale.purpose}
          onChange={(e) => onChange({ ...scale, purpose: e.target.value })}
        />
        <p className="hint">몰리는 시간이나 저장하는 데이터 양을 적어 주면 사양을 더 알맞게 고릅니다.</p>
      </div>

      <div className="compare-note">
        <div>
          <strong>
            {usable.length > 0 ? `연결된 배포 대상 ${usable.length}곳을 모두 비교합니다` : '연결된 배포 대상이 없습니다'}
          </strong>
          <span className="compare-targets">
            {usable.map((c) => (
              <span key={c.id}>
                <ProviderMark provider={c.provider} /> {c.name}
              </span>
            ))}
            {skipped > 0 && <span className="muted">확인이 필요한 {skipped}곳은 빠집니다</span>}
          </span>
        </div>
        <button className="link-btn" onClick={onShowConnections}>
          {usable.length > 0 ? '대상 관리' : '대상 추가하기'}
        </button>
      </div>
    </div>
  )
}

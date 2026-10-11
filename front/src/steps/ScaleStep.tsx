import { ONPREM_ENABLED, isEnabled } from '../providers'
import type { ExpectedUsers, ScaleInput, TrafficPattern } from '../types'

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

/** 입력할 수 있는 최소 월 예산(USD). 기본값도 이 금액 */
export const MIN_BUDGET_USD = 16

const BUDGETS = [MIN_BUDGET_USD, 30, 50, 100]

export const budgetValid = (n: number) => Number.isFinite(n) && n >= MIN_BUDGET_USD

interface Props {
  scale: ScaleInput
  locked: boolean
  onChange: (s: ScaleInput) => void
}

// 관리형 배포: 사용자 AWS 계정 연결은 새 배포의 전제조건이 아님 (docs/PRODUCT_DIRECTION.md §1)
export default function ScaleStep({ scale, locked, onChange }: Props) {
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
              min={MIN_BUDGET_USD}
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
            ? `최소 $${MIN_BUDGET_USD}부터 입력할 수 있습니다. 부하가 가장 많을 때의 월 비용이 이 금액 안에 드는 구성으로 계획합니다.${ONPREM_ENABLED ? ' 온프레미스(로컬 Docker)는 추가 비용 0으로 계산합니다.' : ''}`
            : `$${MIN_BUDGET_USD} 이상의 금액을 넣어 주세요.`}
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
          {isEnabled('aws') ? (
            <>
              <strong>운영자가 준비한 AWS에 배포합니다</strong>
              <span className="hint">
                AWS 계정이나 키가 없어도 됩니다. 플랫폼 운영자가 준비해 둔 AWS 환경에 앱을 올립니다.
                {ONPREM_ENABLED && ' 이 PC의 Docker(온프레미스)로 올리는 안도 함께 비교해 보여 줍니다.'}
              </span>
            </>
          ) : (
            <>
              <strong>이 PC의 Docker에 배포합니다</strong>
              <span className="hint">클라우드 계정이 없어도 됩니다. 플랫폼 worker가 실행 중인 PC의 Docker에 앱을 올립니다(온프레미스).</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

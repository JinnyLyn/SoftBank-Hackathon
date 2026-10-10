import { useState } from 'react'
import { api } from '../api'
import { usd } from '../format'
import type { DomainChoice, DomainMode, DomainQuote } from '../types'

// 소문자 영문·숫자·하이픈, 점으로 나뉜 이름 (예: myapp.com, shop.myapp.co.kr)
const DOMAIN_RE = /^(?=.{4,253}$)(?!-)(?:[a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}$/

export const normalizeDomain = (v: string) =>
  v.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/^www\./, '')

export const domainValid = (v: string) => DOMAIN_RE.test(v)

const MODES: { key: DomainMode; title: string; desc: string }[] = [
  { key: 'own', title: '가지고 있는 도메인', desc: '이미 산 도메인을 연결합니다. 도메인 업체에서 레코드 2개를 추가하면 됩니다.' },
  { key: 'buy', title: '새로 구매', desc: '원하는 이름이 비어 있으면 대신 구매하고 DNS·인증서까지 자동으로 설정합니다.' },
  { key: 'later', title: '나중에', desc: 'AWS가 주는 기본 주소로 먼저 배포합니다. 도메인은 나중에 연결할 수 있습니다.' },
]

interface Props {
  choice: DomainChoice
  quote: DomainQuote | null
  locked: boolean
  onChange: (c: DomainChoice) => void
  onQuote: (q: DomainQuote | null) => void
}

export default function DomainStep({ choice, quote, locked, onChange, onQuote }: Props) {
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState<string | null>(null)
  const name = choice.name
  const valid = domainValid(name)

  const pick = (mode: DomainMode) => {
    onChange({ mode, name: mode === 'later' ? '' : name })
    onQuote(null)
    setCheckError(null)
  }

  const setName = (raw: string) => {
    onChange({ ...choice, name: normalizeDomain(raw) })
    onQuote(null)
    setCheckError(null)
  }

  const check = async (target = name) => {
    setChecking(true)
    setCheckError(null)
    try {
      onQuote(await api.checkDomain(target))
    } catch (e) {
      setCheckError(e instanceof Error ? e.message : String(e))
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="stack">
      <div className="domain-modes" role="radiogroup" aria-label="도메인">
        {MODES.map((m) => (
          <button
            key={m.key}
            role="radio"
            aria-checked={choice.mode === m.key}
            className={'kind' + (choice.mode === m.key ? ' is-on' : '')}
            disabled={locked}
            onClick={() => pick(m.key)}
          >
            <strong>{m.title}</strong>
            <small>{m.desc}</small>
          </button>
        ))}
      </div>

      {choice.mode !== 'later' && (
        <div className="field">
          <label htmlFor="domain-name">{choice.mode === 'own' ? '연결할 도메인' : '사고 싶은 도메인'}</label>
          <div className="domain-input">
            <input
              id="domain-name"
              className="input mono"
              placeholder="myapp.com"
              value={name}
              disabled={locked}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && choice.mode === 'buy' && valid && check()}
              spellCheck={false}
              autoComplete="off"
            />
            {choice.mode === 'buy' && (
              <button className="btn" onClick={() => check()} disabled={!valid || checking || locked}>
                {checking ? '확인 중…' : '구매 가능 확인'}
              </button>
            )}
          </div>
          {name && !valid && <p className="conn-error">myapp.com 처럼 점이 들어간 도메인 이름을 넣어 주세요.</p>}
          {choice.mode === 'own' && valid && (
            <p className="hint">
              배포가 끝나면 진행 화면에 도메인 업체(가비아, Cloudflare 등)에 넣을 레코드가 나옵니다. 넣으면 자동으로 확인하고
              HTTPS 인증서를 발급합니다.
            </p>
          )}
        </div>
      )}

      {checkError && <p className="conn-error">{checkError}</p>}

      {choice.mode === 'buy' && quote && quote.name === name && (
        <div className={'domain-quote' + (quote.available ? ' is-ok' : ' is-no')}>
          {quote.available ? (
            <>
              <strong>{quote.name} 구매할 수 있습니다</strong>
              <span>
                1년 {quote.priceUsdPerYear !== null ? usd(quote.priceUsdPerYear) : '가격 확인 불가'} · 비용 승인 단계에서 다시
                확인합니다. 구매는 취소·환불되지 않습니다.
              </span>
            </>
          ) : (
            <>
              <strong>{quote.name}은(는) 구매할 수 없습니다</strong>
              {quote.reason && <span>{quote.reason}</span>}
              {quote.suggestions && quote.suggestions.length > 0 && (
                <span className="domain-suggest">
                  이건 어떨까요?
                  {quote.suggestions.map((s) => (
                    <button
                      key={s}
                      className="link-btn"
                      disabled={locked}
                      onClick={() => {
                        onChange({ ...choice, name: s })
                        check(s)
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </span>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** 다음 단계로 갈 수 있는 상태인지 */
export function domainReady(choice: DomainChoice, quote: DomainQuote | null): boolean {
  if (choice.mode === 'later') return true
  if (!domainValid(choice.name)) return false
  if (choice.mode === 'own') return true
  return Boolean(quote && quote.name === choice.name && quote.available)
}

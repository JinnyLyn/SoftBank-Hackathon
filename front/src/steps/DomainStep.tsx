import { isEnabled } from '../providers'
import type { DomainChoice, DomainMode } from '../types'
import { useT } from '../i18n'

// 소문자 영문·숫자·하이픈, 점으로 나뉜 이름 (예: myapp.com, shop.myapp.co.kr)
const DOMAIN_RE = /^(?=.{4,253}$)(?!-)(?:[a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}$/

export const normalizeDomain = (v: string) =>
  v.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/^www\./, '')

export const domainValid = (v: string) => DOMAIN_RE.test(v)

// auto: 플랫폼 도메인 아래 겹치지 않는 주소를 자동으로 만듦 (Vercel처럼). 주소는 서버가 정함
const MODES: { key: DomainMode; title: string; desc: string }[] = [
  {
    key: 'auto',
    title: '자동 주소 (추천)',
    desc: '플랫폼 도메인 아래에 앱마다 겹치지 않는 https 주소를 바로 만들어 드립니다. 내 도메인은 아니며, 가지고 있는 도메인은 나중에 연결할 수 있습니다.',
  },
  { key: 'own', title: '가지고 있는 도메인', desc: '이미 산 도메인을 연결합니다. 도메인 업체에서 레코드 2개를 추가하면 됩니다.' },
]

interface Props {
  choice: DomainChoice
  locked: boolean
  onChange: (c: DomainChoice) => void
}

export default function DomainStep({ choice, locked, onChange }: Props) {
  const name = choice.name
  const valid = domainValid(name)
  const t = useT()

  // 온프레미스(로컬 Docker)만 켰으면 도메인·HTTPS를 만들 수 없다. 고를 수 없는 선택지를 보여 주지 않고 이유만 알린다
  // (기본값 auto는 그대로 두지만 백엔드가 도메인 기능이 없어 미리보기 주소로 진행한다)
  if (!isEnabled('aws'))
    return (
      <div className="compare-note">
        <div>
          <strong>{t('온프레미스(로컬 Docker)는 도메인·HTTPS를 지원하지 않습니다')}</strong>
          <span className="hint">{t('이 PC의 로컬 주소(http://localhost:포트)로만 접속합니다. 도메인은 연결되지 않습니다.')}</span>
        </div>
      </div>
    )

  return (
    <div className="stack">
      <div className="domain-modes" role="radiogroup" aria-label={t('도메인')}>
        {MODES.map((m) => (
          <button
            key={m.key}
            role="radio"
            aria-checked={choice.mode === m.key}
            className={'kind' + (choice.mode === m.key ? ' is-on' : '')}
            disabled={locked}
            onClick={() => onChange({ mode: m.key, name: m.key === 'auto' ? '' : name })}
          >
            <strong>{t(m.title)}</strong>
            <small>{t(m.desc)}</small>
          </button>
        ))}
      </div>

      {choice.mode === 'auto' && (
        <p className="hint">{t('주소는 분석이 끝나면 정해지고, 비용 승인 화면에서 확인할 수 있습니다. HTTPS 인증서는 플랫폼이 관리합니다.')}</p>
      )}

      {choice.mode === 'own' && (
        <div className="field">
          <label htmlFor="domain-name">{t('연결할 도메인')}</label>
          <div className="domain-input">
            <input
              id="domain-name"
              className="input mono"
              placeholder="myapp.com"
              value={name}
              disabled={locked}
              onChange={(e) => onChange({ ...choice, name: normalizeDomain(e.target.value) })}
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          {name && !valid && <p className="conn-error">{t('myapp.com 처럼 점이 들어간 도메인 이름을 넣어 주세요.')}</p>}
          {valid && (
            <p className="hint">
              {t('배포가 끝나면 진행 화면에 도메인 업체(가비아, Cloudflare 등)에 넣을 레코드가 나옵니다. 넣으면 자동으로 확인하고 HTTPS 인증서를 발급합니다.')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** 다음 단계로 갈 수 있는 상태인지 */
export function domainReady(choice: DomainChoice): boolean {
  return choice.mode === 'auto' || domainValid(choice.name)
}

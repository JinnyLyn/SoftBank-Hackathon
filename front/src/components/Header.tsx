import { isEnabled } from '../providers'
import { LangSwitch, useT } from '../i18n'

export type Page = 'new' | 'history'

/** 새 배포가 올라갈 곳. 켜 둔 배포 대상(VITE_PROVIDERS)을 따름 */
const DEPLOY_TARGET = [isEnabled('aws') && '관리형 AWS', isEnabled('onprem') && '이 PC의 Docker'].filter(Boolean).join(' · ')

const TABS: { key: Page; label: string }[] = [
  { key: 'new', label: '새 배포' },
  { key: 'history', label: '배포 이력' },
]

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="5" fill="#f2b705" />
      <path d="M6.5 22 10.6 11h2.8l4.1 11z" fill="#26282b" />
      <path d="M12 20.5v-2.2M12 16.4v-1.8M12 13v-1.2" stroke="#f2b705" strokeWidth="1.1" />
      <path d="M7.6 9.2a2.3 2.3 0 0 1 2.1-3.1 3.2 3.2 0 0 1 6 .6 1.9 1.9 0 0 1 .7 2.5z" fill="#26282b" />
    </svg>
  )
}

interface Props {
  page: Page
  onChange: (p: Page) => void
}

export default function Header({ page, onChange }: Props) {
  const t = useT()

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button className="brand" onClick={() => onChange('new')}>
          <Logo />
          <span>Paved Clouds</span>
        </button>
        <nav className="tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              className={'tab' + (page === tab.key ? ' is-active' : '')}
              onClick={() => onChange(tab.key)}
            >
              {t(tab.label)}
            </button>
          ))}
        </nav>
        {/* 배포는 운영자가 준비한 관리형 AWS. 사용자 계정 연결은 없음 */}
        <span className="account">
          <span className="dot" />
          {t('배포: {target}', { target: DEPLOY_TARGET.split(' · ').map((x) => t(x)).join(' · ') })}
        </span>
        <LangSwitch />
      </div>
    </header>
  )
}

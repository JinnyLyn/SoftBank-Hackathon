import { NEEDS_CONNECTION } from '../api'
import type { Connection } from '../types'

export type Page = 'new' | 'history' | 'targets'

const TABS: { key: Page; label: string }[] = [
  { key: 'new', label: '새 배포' },
  { key: 'history', label: '배포 이력' },
  { key: 'targets', label: '연결 관리' },
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
  connections: Connection[]
  /** 연결 목록 조회 실패 (0곳과 구분) */
  loadFailed: boolean
}

export default function Header({ page, onChange, connections, loadFailed }: Props) {
  const broken = connections.filter((c) => c.status !== 'connected').length

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button className="brand" onClick={() => onChange('new')}>
          <Logo />
          <span>Paved Clouds</span>
        </button>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={'tab' + (page === t.key ? ' is-active' : '')}
              onClick={() => onChange(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <button className="account" onClick={() => onChange('targets')} title="연결 관리">
          <span className={'dot' + (NEEDS_CONNECTION && (broken || loadFailed) ? ' is-warn' : '')} />
          {!NEEDS_CONNECTION
            ? '배포: 팀 AWS 계정'
            : loadFailed
              ? '연결 정보 조회 실패'
              : `대상 ${connections.length}곳${broken ? ` · 확인 필요 ${broken}` : ''}`}
        </button>
      </div>
    </header>
  )
}

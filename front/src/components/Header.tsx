export type Page = 'new' | 'history' | 'targets'

const TABS: { key: Page; label: string }[] = [
  { key: 'new', label: '새 배포' },
  { key: 'history', label: '배포 이력' },
  { key: 'targets', label: '배포 대상 설정' },
]

export default function Header({ page, onChange }: { page: Page; onChange: (p: Page) => void }) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button className="brand" onClick={() => onChange('new')}>
          <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
            <rect width="24" height="24" rx="5" fill="currentColor" />
            <path d="M5 14h14l-2 4H7z M12 5v8 M12 5l4 6h-4" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
          </svg>
          OneShip
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
      </div>
    </header>
  )
}

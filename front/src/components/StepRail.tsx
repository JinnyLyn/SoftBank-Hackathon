export type RailState = 'done' | 'current' | 'todo' | 'failed'

export interface RailItem {
  label: string
  sub?: string
  state: RailState
  /** 이동 가능 여부 */
  enabled: boolean
}

export default function StepRail({ items, onSelect }: { items: RailItem[]; onSelect: (i: number) => void }) {
  return (
    <ol className="rail">
      {items.map((it, i) => (
        <li key={it.label} className={'rail-item is-' + it.state}>
          <button disabled={!it.enabled} onClick={() => onSelect(i)}>
            <span className="rail-node" aria-hidden>
              {it.state === 'done' ? (
                <svg width="10" height="10" viewBox="0 0 10 10">
                  <path d="M1.5 5.2 4 7.5 8.5 2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
                </svg>
              ) : it.state === 'failed' ? (
                '!'
              ) : (
                i + 1
              )}
            </span>
            <span className="rail-text">
              <strong>{it.label}</strong>
              {it.sub && <small>{it.sub}</small>}
            </span>
          </button>
        </li>
      ))}
    </ol>
  )
}

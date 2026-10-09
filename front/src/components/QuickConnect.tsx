import { useRef, useState } from 'react'
import { api } from '../api'
import { isSafeRedirect } from '../api/http'
import InstallCommand from './InstallCommand'
import { CONSOLE_HOSTS, ENABLED_PROVIDERS, PROVIDERS } from '../providers'
import type { Connection, Provider } from '../types'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

interface Props {
  connections: Connection[]
  onChange: (list: Connection[]) => void
}

/**
 * 연결된 배포 대상이 없을 때 새 배포 화면 안에서 바로 연결.
 * 연결 관리 탭과 같은 API를 쓰고, 이름은 기본값으로 정함 (나중에 연결 관리에서 바꿀 수 있음)
 */
export default function QuickConnect({ connections, onChange }: Props) {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [busy, setBusy] = useState<Provider | 'check' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const listRef = useRef(connections)
  listRef.current = connections

  const replace = (c: Connection) => onChange(listRef.current.map((x) => (x.id === c.id ? c : x)))
  const active = connections.find((c) => c.id === activeId) ?? null

  const start = async (provider: Provider) => {
    setBusy(provider)
    setError(null)
    try {
      const n = connections.filter((c) => c.provider === provider).length + 1
      const name = provider === 'aws' ? `AWS ${n}` : `사내 서버 ${n}`
      const c = await api.saveConnection({ provider, name, fields: {} })
      onChange([...listRef.current, c])
      setActiveId(c.id)
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  const check = async (id: string) => {
    const c = await api.checkConnection(id)
    replace(c)
    return c
  }

  const checkAws = async () => {
    if (!active) return
    setBusy('check')
    setError(null)
    try {
      const c = await check(active.id)
      if (c.status !== 'connected') setError('아직 스택 생성이 끝나지 않았습니다. 콘솔에서 완료된 뒤 다시 눌러 주세요.')
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  if (active?.provider === 'aws' && active.status === 'pending') {
    const safe = active.setupUrl && isSafeRedirect(active.setupUrl, CONSOLE_HOSTS)
    return (
      <div className="quick">
        <strong>AWS 계정 연결</strong>
        <ol className="install-steps">
          <li>
            <strong>
              {safe ? (
                <a href={active.setupUrl} target="_blank" rel="noreferrer noopener">
                  AWS 콘솔에서 연결 스택 만들기
                </a>
              ) : (
                '콘솔 주소를 받지 못했습니다'
              )}
            </strong>
            <span>열린 화면에서 "스택 생성"만 누르면 Paved Clouds용 역할이 만들어집니다.</span>
          </li>
          <li>
            <strong>스택 생성이 끝나면 아래 버튼</strong>
            <span>계정 ID는 자동으로 읽습니다.</span>
          </li>
        </ol>
        {error && <p className="conn-error">{error}</p>}
        <div className="quick-actions">
          <button className="link-btn" onClick={() => setActiveId(null)}>
            다른 방법 고르기
          </button>
          <button className="btn btn-primary" onClick={checkAws} disabled={busy !== null}>
            {busy === 'check' ? '확인 중…' : '연결 확인'}
          </button>
        </div>
      </div>
    )
  }

  if (active?.provider === 'onprem' && active.status === 'pending') {
    return (
      <div className="quick">
        <strong>사내 서버 연결</strong>
        <InstallCommand
          key={active.id}
          conn={active}
          onCheck={() => check(active.id)}
          onReissue={async () => {
            replace(await api.saveConnection({ id: active.id, provider: 'onprem', name: active.name, fields: {} }))
          }}
          onClose={() => setActiveId(null)}
        />
        <div className="quick-actions">
          <button className="link-btn" onClick={() => setActiveId(null)}>
            다른 방법 고르기
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="quick">
      <strong>{ENABLED_PROVIDERS.length > 1 ? '배포할 곳을 먼저 연결해 주세요' : 'AWS 계정을 먼저 연결해 주세요'}</strong>
      <span className="hint">한 번 연결해 두면 다음 배포부터는 이 단계가 나오지 않습니다.</span>
      <div className={'quick-choices' + (ENABLED_PROVIDERS.length === 1 ? ' is-single' : '')}>
        {ENABLED_PROVIDERS.map((p) => (
          <button key={p} className="kind" onClick={() => start(p)} disabled={busy !== null}>
            <strong>{p === 'aws' ? 'AWS 계정 연결' : '사내 서버 연결'}</strong>
            <small>{busy === p ? '준비 중…' : PROVIDERS[p].summary}</small>
          </button>
        ))}
      </div>
      {error && <p className="conn-error">{error}</p>}
    </div>
  )
}

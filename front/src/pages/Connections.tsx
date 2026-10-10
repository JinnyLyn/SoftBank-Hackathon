import { useRef, useState } from 'react'
import { api } from '../api'
import { shortTime } from '../format'
import { isSafeRedirect } from '../api/http'
import ProviderMark from '../components/ProviderMark'
import InstallCommand from '../components/InstallCommand'
import LoadError from '../components/LoadError'
import { CONSOLE_HOSTS, ENABLED_PROVIDERS, EXTERNAL_ID, ONPREM_ENABLED, PROVIDERS } from '../providers'
import type { Connection, Provider } from '../types'

interface Draft {
  id?: string
  provider: Provider
  name: string
  fields: Record<string, string>
}

const emptyDraft = (provider: Provider, count: number): Draft => ({
  provider,
  name: `${PROVIDERS[provider].label} ${count + 1}`,
  fields: Object.fromEntries(
    PROVIDERS[provider].fields.map((f) => [f.key, f.options?.[0].value ?? (f.key === 'port' ? '22' : '')]),
  ),
})

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

interface Props {
  connections: Connection[] | null
  loadError: string | null
  onReload: () => void
  onChange: (list: Connection[]) => void
}

export default function Connections({ connections, loadError, onReload, onChange }: Props) {
  const list = connections ?? []
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(ENABLED_PROVIDERS[0], 0))
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 설치 명령 화면을 띄울 온프레미스 연결
  const [installFor, setInstallFor] = useState<string | null>(null)
  // 폴링 콜백이 오래된 목록을 덮어쓰지 않도록 항상 최신 목록을 봄
  const listRef = useRef(list)
  listRef.current = list
  const replace = (c: Connection) => onChange(listRef.current.map((x) => (x.id === c.id ? c : x)))

  const installConn = list.find((c) => c.id === installFor) ?? null

  const meta = PROVIDERS[draft.provider]
  const sameKind = list.filter((c) => c.provider === draft.provider).length
  const valid = draft.name.trim() !== '' && meta.fields.every((f) => f.optional || draft.fields[f.key]?.trim())

  const pickProvider = (p: Provider) =>
    setDraft(emptyDraft(p, list.filter((c) => c.provider === p).length))

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const saved = await api.saveConnection(draft)
      onChange(draft.id ? list.map((c) => (c.id === saved.id ? saved : c)) : [...list, saved])
      pickProvider(draft.provider)
      if (saved.provider === 'onprem' && saved.status === 'pending') setInstallFor(saved.id)
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  const check = async (id: string) => {
    setChecking(id)
    try {
      replace(await api.checkConnection(id))
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setChecking(null)
    }
  }

  const remove = async (c: Connection) => {
    if (!window.confirm(`'${c.name}' 연결을 지울까요? 이미 배포된 리소스는 그대로 남습니다.`)) return
    try {
      await api.deleteConnection(c.id)
      onChange(list.filter((x) => x.id !== c.id))
      if (draft.id === c.id) pickProvider(draft.provider)
    } catch (e) {
      setError(errMsg(e))
    }
  }

  const pollInstall = async (id: string) => {
    const c = await api.checkConnection(id)
    replace(c)
    return c
  }

  const reissueInstall = async (c: Connection) => {
    try {
      replace(await api.saveConnection({ id: c.id, provider: c.provider, name: c.name, fields: c.fields }))
    } catch (e) {
      setError(errMsg(e))
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>연결 관리</h1>
          <p className="readonly-note">
            새 배포에는 연결이 필요 없습니다. 앱은 플랫폼 운영자가 준비한 AWS에 배포됩니다. 이 화면은 운영자가 AWS 계정을
            역할 위임으로 연결할 때 씁니다.
          </p>
          <p>
            {ONPREM_ENABLED
              ? 'AWS 계정과 사내 서버를 등록해 두면 분석할 때 모든 대상의 구성과 비용을 같이 비교합니다. AWS는 키 대신 역할 위임으로, 사내 서버는 설치 명령 한 줄로 연결합니다.'
              : 'AWS 계정을 등록해 두면 분석할 때 구성과 비용을 비교합니다. 액세스 키 대신 역할 위임으로 연결합니다.'}
          </p>
        </div>
      </div>

      <div className="conn-layout">
        <section className="panel">
          <header className="list-head">
            <h2>연결된 대상 {list.length}곳</h2>
          </header>
          {loadError && (
            <div className="pad">
              <LoadError what="연결 목록" message={loadError} onRetry={onReload} />
            </div>
          )}
          {!loadError && connections === null && <p className="muted pad">불러오는 중…</p>}
          {!loadError && connections !== null && list.length === 0 && (
            <p className="muted pad">아직 없습니다. 오른쪽에서 첫 배포 대상을 추가하세요.</p>
          )}
          <ul className="conn-list">
            {list.map((c) => (
              <li key={c.id} className={draft.id === c.id || installFor === c.id ? 'is-editing' : ''}>
                <ProviderMark provider={c.provider} />
                <div className="conn-main">
                  <strong>{c.name}</strong>
                  <span className="mono small muted">{c.detail}</span>
                  {c.roleArn && (
                    <span className="mono small muted conn-role" title={c.roleArn}>
                      역할 {c.roleArn}
                    </span>
                  )}
                  {c.status === 'error' && <span className="conn-error">{c.error}</span>}
                  {c.status === 'pending' && c.provider === 'onprem' && (
                    <span className="conn-pending">
                      서버에서 설치 명령을 실행하면 자동으로 연결됩니다.{' '}
                      <button className="link-btn" onClick={() => setInstallFor(c.id)}>
                        명령 보기
                      </button>
                    </span>
                  )}
                  {c.status === 'pending' && c.provider !== 'onprem' && c.roleArn && (
                    <span className="conn-pending">
                      스택이 역할을 만들었습니다. 플랫폼이 이 역할로 접속되는지 확인하는 중입니다 → 잠시 뒤 "다시 확인"
                    </span>
                  )}
                  {c.status === 'pending' && c.provider !== 'onprem' && !c.roleArn && (
                    <span className="conn-pending">
                      {c.setupUrl && isSafeRedirect(c.setupUrl, CONSOLE_HOSTS) ? (
                        <a href={c.setupUrl} target="_blank" rel="noreferrer noopener">
                          콘솔에서 연결 스택 만들기
                        </a>
                      ) : (
                        '콘솔 연결 링크가 아직 준비되지 않았습니다 (서버의 AWS 연결 템플릿 설정 필요).'
                      )}
                      {c.setupUrl ? ' → 끝나면 "다시 확인"' : ''}
                    </span>
                  )}
                </div>
                <div className="conn-side">
                  <span className={'conn-status is-' + c.status}>
                    {{ connected: '연결됨', pending: '연결 대기', error: '확인 필요' }[c.status]}
                  </span>
                  <span className="small muted">{shortTime(c.checkedAt)} 확인</span>
                </div>
                <div className="conn-actions">
                  <button className="btn btn-ghost btn-sm" onClick={() => check(c.id)} disabled={checking === c.id}>
                    {checking === c.id ? '확인 중…' : '다시 확인'}
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setDraft({ id: c.id, provider: c.provider, name: c.name, fields: { ...c.fields } })}
                  >
                    수정
                  </button>
                  <button className="btn btn-ghost btn-sm danger" onClick={() => remove(c)}>
                    삭제
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {installConn && (
          <section className="panel conn-form">
            <header className="list-head">
              <h2>'{installConn.name}' 서버 연결</h2>
              <button className="link-btn" onClick={() => setInstallFor(null)}>
                닫기
              </button>
            </header>
            <div className="panel-body">
              <InstallCommand
                key={installConn.id}
                conn={installConn}
                onCheck={() => pollInstall(installConn.id)}
                onReissue={() => reissueInstall(installConn)}
                onClose={() => {
                  setInstallFor(null)
                  pickProvider('onprem')
                }}
              />
            </div>
          </section>
        )}

        {!installConn && (
        <section className="panel conn-form">
          <header className="list-head">
            <h2>{draft.id ? `'${draft.name}' 수정` : '새 배포 대상'}</h2>
            {draft.id && (
              <button className="link-btn" onClick={() => pickProvider(draft.provider)}>
                새로 추가하기
              </button>
            )}
          </header>
          <div className="panel-body stack">
            {ENABLED_PROVIDERS.length > 1 && (
            <div className="kind-grid" role="radiogroup" aria-label="종류">
              {ENABLED_PROVIDERS.map((p) => (
                <button
                  key={p}
                  role="radio"
                  aria-checked={draft.provider === p}
                  className={'kind' + (draft.provider === p ? ' is-on' : '')}
                  onClick={() => pickProvider(p)}
                  disabled={Boolean(draft.id)}
                >
                  <strong>{PROVIDERS[p].label}</strong>
                  <small>{PROVIDERS[p].summary}</small>
                </button>
              ))}
            </div>
            )}

            {meta.howto.length > 0 && (
            <ol className="howto">
              {meta.howto.map((h) => {
                const text = h
                return (
                  <li key={h}>
                    {text.split('{externalId}').map((part, i) =>
                      i === 0 ? part : (
                        <span key={i}>
                          <code>{EXTERNAL_ID}</code>
                          {part}
                        </span>
                      ),
                    )}
                  </li>
                )
              })}
            </ol>
            )}

            {draft.provider === 'onprem' && !draft.id && (
              <p className="install-intro">
                이름만 정하고 <strong>설치 명령 만들기</strong>를 누르세요. 서버에서 실행할 명령 한 줄이 나옵니다. Docker가
                없어도 알아서 설치합니다.
              </p>
            )}

            <div className="form-grid">
              <div className="field span-2">
                <label htmlFor="c-name">이름</label>
                <input
                  id="c-name"
                  className="input"
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </div>
              {meta.fields.map((f) => (
                <div key={f.key} className={'field' + (f.wide ? ' span-2' : '')}>
                  <label htmlFor={'c-' + f.key}>
                    {f.label}
                    {f.optional && <span className="muted"> (선택)</span>}
                  </label>
                  {f.options ? (
                    <select
                      id={'c-' + f.key}
                      className="input"
                      value={draft.fields[f.key] ?? ''}
                      onChange={(e) => setDraft({ ...draft, fields: { ...draft.fields, [f.key]: e.target.value } })}
                    >
                      {f.options.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={'c-' + f.key}
                      className="input mono"
                      placeholder={f.placeholder}
                      value={draft.fields[f.key] ?? ''}
                      spellCheck={false}
                      onChange={(e) => setDraft({ ...draft, fields: { ...draft.fields, [f.key]: e.target.value } })}
                    />
                  )}
                  {f.hint && <p className="hint">{f.hint}</p>}
                </div>
              ))}
            </div>
            {error && <p className="conn-error">{error}</p>}
          </div>
          <footer className="panel-foot">
            <span className="hint">{sameKind > 0 && !draft.id ? `${meta.label} 대상이 이미 ${sameKind}곳 있습니다.` : ''}</span>
            <button className="btn btn-primary" onClick={save} disabled={!valid || saving}>
              {saving
                ? '저장 중…'
                : draft.id
                  ? '저장'
                  : draft.provider === 'aws'
                    ? '저장하고 콘솔 링크 받기'
                    : draft.provider === 'onprem'
                      ? '설치 명령 만들기'
                      : '연결 확인하고 저장'}
            </button>
          </footer>
        </section>
        )}
      </div>
    </div>
  )
}

import { useState } from 'react'
import { api } from '../api'
import ProviderMark from '../components/ProviderMark'
import { EXTERNAL_ID, PROVIDER_ORDER, PROVIDERS, PUBLIC_KEY } from '../providers'
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
  /** 관리자만 추가, 수정, 삭제 가능 */
  canEdit: boolean
  onChange: (list: Connection[]) => void
}

export default function Connections({ connections, canEdit, onChange }: Props) {
  const list = connections ?? []
  const [draft, setDraft] = useState<Draft>(() => emptyDraft('aws', 0))
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

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
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  const check = async (id: string) => {
    setChecking(id)
    try {
      const c = await api.checkConnection(id)
      onChange(list.map((x) => (x.id === id ? c : x)))
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

  const copyKey = async () => {
    try {
      await navigator.clipboard.writeText(PUBLIC_KEY)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // 클립보드 권한이 없으면 사용자가 직접 복사
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>배포 대상</h1>
          <p>
            어디든 등록해 두면 분석할 때 모든 대상의 구성과 비용을 같이 비교합니다. 클라우드는 키 대신 권한 위임으로,
            서버는 SSH로 연결합니다.
          </p>
        </div>
      </div>

      {!canEdit && (
        <p className="readonly-note">
          배포 대상 추가와 수정은 관리자만 할 수 있습니다. 필요한 대상이 있으면 관리자에게 요청하세요.
        </p>
      )}

      <div className={'conn-layout' + (canEdit ? '' : ' is-readonly')}>
        <section className="panel">
          <header className="list-head">
            <h2>연결된 대상 {list.length}곳</h2>
          </header>
          {connections === null && <p className="muted pad">불러오는 중…</p>}
          {connections !== null && list.length === 0 && (
            <p className="muted pad">아직 없습니다. 오른쪽에서 첫 배포 대상을 추가하세요.</p>
          )}
          <ul className="conn-list">
            {list.map((c) => (
              <li key={c.id} className={draft.id === c.id ? 'is-editing' : ''}>
                <ProviderMark provider={c.provider} />
                <div className="conn-main">
                  <strong>{c.name}</strong>
                  <span className="mono small muted">{c.detail}</span>
                  {c.status === 'error' && <span className="conn-error">{c.error}</span>}
                </div>
                <div className="conn-side">
                  <span className={'conn-status is-' + c.status}>
                    {c.status === 'connected' ? '연결됨' : '확인 필요'}
                  </span>
                  <span className="small muted">{c.checkedAt.slice(5)} 확인</span>
                </div>
                <div className="conn-actions">
                  <button className="btn btn-ghost btn-sm" onClick={() => check(c.id)} disabled={checking === c.id}>
                    {checking === c.id ? '확인 중…' : '다시 확인'}
                  </button>
                  {canEdit && (
                    <>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => setDraft({ id: c.id, provider: c.provider, name: c.name, fields: { ...c.fields } })}
                      >
                        수정
                      </button>
                      <button className="btn btn-ghost btn-sm danger" onClick={() => remove(c)}>
                        삭제
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>

        {canEdit && (
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
            <div className="kind-grid" role="radiogroup" aria-label="종류">
              {PROVIDER_ORDER.map((p) => (
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

            <ol className="howto">
              {meta.howto.map((h) => {
                const [text, extra] = h.split('\n')
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
                    {extra === '{publicKey}' && (
                      <div className="key-box">
                        <code>{PUBLIC_KEY}</code>
                        <button className="link-btn" onClick={copyKey}>
                          {copied ? '복사됨' : '복사'}
                        </button>
                      </div>
                    )}
                  </li>
                )
              })}
            </ol>

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
              {saving ? '연결 확인 중…' : '연결 확인하고 저장'}
            </button>
          </footer>
        </section>
        )}
      </div>
    </div>
  )
}

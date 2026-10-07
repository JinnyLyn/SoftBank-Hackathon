import { useState } from 'react'
import { api } from '../api'
import { Logo } from '../components/Header'
import type { Session, SsoDiscovery } from '../types'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface Props {
  /** 로그아웃된 이유 (만료, 무활동 등) */
  notice?: string
  onLogin: (s: Session) => void
}

export default function Login({ notice, onLogin }: Props) {
  const [email, setEmail] = useState('')
  const [found, setFound] = useState<SsoDiscovery | null>(null)
  const [busy, setBusy] = useState<null | 'discover' | 'redirect'>(null)
  const [error, setError] = useState<string | null>(null)

  const valid = EMAIL_RE.test(email.trim())

  const discover = async () => {
    setBusy('discover')
    setError(null)
    try {
      setFound(await api.discover(email.trim()))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const go = async () => {
    if (!found) return
    setBusy('redirect')
    setError(null)
    try {
      onLogin(await api.startSso(email.trim(), found))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(null)
    }
  }

  return (
    <div className="login">
      <aside className="login-side">
        <div className="login-brand">
          <Logo size={28} />
          Paved Clouds
        </div>
        <div className="login-copy">
          <h2>코드만 올리면 길은 깔아 둘게요.</h2>
          <p>분석, 구성 추천, 비용 비교, 승인 후 배포까지. 클라우드든 사내 서버든 같은 방식으로.</p>
        </div>
        <svg className="login-road" viewBox="0 0 400 160" preserveAspectRatio="none" aria-hidden>
          <path d="M150 160 190 0h20l40 160z" fill="#34373b" />
          <path d="M200 150v-22M200 112v-18M200 80v-14M200 54v-10M200 34v-7M200 18v-5" stroke="#f2b705" strokeWidth="3" />
        </svg>
      </aside>

      <main className="login-main">
        <form
          className="login-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (found) go()
            else if (valid) discover()
          }}
        >
          <h1>로그인</h1>
          <p className="muted">회사 계정(SSO)으로만 로그인할 수 있습니다.</p>

          {notice && <p className="login-notice">{notice}</p>}

          <div className="field">
            <label htmlFor="email">회사 이메일</label>
            <input
              id="email"
              type="email"
              className="input"
              autoComplete="username"
              placeholder="name@company.com"
              value={email}
              disabled={busy !== null}
              onChange={(e) => {
                setEmail(e.target.value)
                setFound(null)
              }}
              autoFocus
            />
          </div>

          {found && (
            <div className="idp-card">
              <span className="muted small">조직</span>
              <strong>{found.org}</strong>
              <span className="small">
                {found.idp} · {found.protocol.toUpperCase()}
              </span>
            </div>
          )}

          {error && <p className="conn-error">{error}</p>}

          <button className="btn btn-primary btn-block" type="submit" disabled={!valid || busy !== null}>
            {busy === 'discover'
              ? '조직 찾는 중…'
              : busy === 'redirect'
                ? `${found?.idp}로 이동 중…`
                : found
                  ? `${found.idp}로 계속`
                  : '다음'}
          </button>

          <p className="hint">
            조직에 SSO가 설정되어 있지 않으면 관리자에게 문의하세요. 비밀번호는 Paved Clouds에 저장하지 않습니다.
          </p>
        </form>
      </main>
    </div>
  )
}

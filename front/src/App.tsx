import { useEffect, useState } from 'react'
import Header, { type Page } from './components/Header'
import NewDeploy from './pages/NewDeploy'
import History from './pages/History'
import Connections from './pages/Connections'
import Login from './pages/Login'
import { api, applySession, onUnauthorized } from './api'
import { useIdleTimeout } from './useIdleTimeout'
import type { Connection, Session } from './types'

const IDLE_MS = 30 * 60 * 1000

export default function App() {
  // undefined: 세션 확인 중, null: 로그인 필요
  const [session, setSession] = useState<Session | null | undefined>(undefined)
  const [notice, setNotice] = useState<string>()
  const [page, setPage] = useState<Page>('new')
  const [connections, setConnections] = useState<Connection[] | null>(null)

  useEffect(() => {
    api
      .session()
      .then((s) => {
        if (s) applySession(s)
        setSession(s)
      })
      .catch(() => setSession(null))
  }, [])

  // 어떤 요청이든 401이 오면 로그인 화면으로
  useEffect(
    () =>
      onUnauthorized(() => {
        setSession(null)
        setNotice('로그인이 만료되었습니다. 다시 로그인해 주세요.')
      }),
    [],
  )

  useEffect(() => {
    if (!session) return
    api.listConnections().then(setConnections).catch(() => setConnections([]))
  }, [session])

  const logout = async (reason?: string) => {
    await api.logout().catch(() => {})
    setSession(null)
    setConnections(null)
    setNotice(reason)
  }

  useIdleTimeout(IDLE_MS, () => logout('30분 동안 사용하지 않아 자동으로 로그아웃했습니다.'), Boolean(session))

  if (session === undefined) return <div className="splash" aria-busy="true" />

  if (!session)
    return (
      <Login
        notice={notice}
        onLogin={(s) => {
          applySession(s)
          setSession(s)
          setNotice(undefined)
          setPage('new')
        }}
      />
    )

  return (
    <>
      <Header page={page} onChange={setPage} connections={connections ?? []} user={session.user} onLogout={() => logout()} />
      <main className="container">
        {/* 탭을 옮겨도 진행 중인 배포 상태가 날아가지 않게 숨기기만 함 */}
        <div hidden={page !== 'new'}>
          <NewDeploy
            connections={connections ?? []}
            userName={session.user.name}
            onShowHistory={() => setPage('history')}
            onShowConnections={() => setPage('targets')}
          />
        </div>
        {page === 'history' && <History />}
        {page === 'targets' && (
          <Connections connections={connections} canEdit={session.user.role === 'admin'} onChange={setConnections} />
        )}
      </main>
    </>
  )
}

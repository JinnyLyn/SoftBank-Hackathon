import { useEffect, useState } from 'react'
import Header, { type Page } from './components/Header'
import NewDeploy from './pages/NewDeploy'
import History from './pages/History'
import Connections from './pages/Connections'
import { api, IS_MOCK } from './api'
import { isEnabled } from './providers'
import type { Connection } from './types'

export default function App() {
  const [page, setPage] = useState<Page>('new')
  const [connections, setConnections] = useState<Connection[] | null>(null)

  useEffect(() => {
    // 꺼 둔 종류(VITE_PROVIDERS에 없는 것)는 백엔드가 돌려줘도 화면에서 뺌
    api
      .listConnections()
      .then((list) => setConnections(list.filter((c) => isEnabled(c.provider))))
      .catch(() => setConnections([]))
  }, [])

  return (
    <>
      <Header page={page} onChange={setPage} connections={connections ?? []} />
      {IS_MOCK && (
        <div className="mock-banner" role="note">
          <strong>MOCK</strong> 백엔드 없이 예시 데이터로 동작합니다. 분석, 비용, 배포 결과는 실제가 아닙니다.
        </div>
      )}
      <main className="container">
        {/* 탭을 옮겨도 진행 중인 배포 상태가 날아가지 않게 숨기기만 함 */}
        <div hidden={page !== 'new'}>
          <NewDeploy
            connections={connections ?? []}
            onConnectionsChange={setConnections}
            onShowHistory={() => setPage('history')}
            onShowConnections={() => setPage('targets')}
          />
        </div>
        {page === 'history' && <History />}
        {page === 'targets' && <Connections connections={connections} onChange={setConnections} />}
      </main>
    </>
  )
}

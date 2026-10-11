import { useState } from 'react'
import Header, { type Page } from './components/Header'
import NewDeploy from './pages/NewDeploy'
import History from './pages/History'
import { IS_MOCK } from './api'
import { useT } from './i18n'

export default function App() {
  const [page, setPage] = useState<Page>('new')
  const t = useT()

  return (
    <>
      <Header page={page} onChange={setPage} />
      {IS_MOCK && (
        <div className="mock-banner" role="note">
          <strong>MOCK</strong> {t('백엔드 없이 예시 데이터로 동작합니다. 분석, 비용, 배포 결과는 실제가 아닙니다.')}
        </div>
      )}
      <main className="container">
        {/* 탭을 옮겨도 진행 중인 배포 상태가 날아가지 않게 숨기기만 함 */}
        <div hidden={page !== 'new'}>
          <NewDeploy onShowHistory={() => setPage('history')} />
        </div>
        {page === 'history' && <History />}
      </main>
    </>
  )
}

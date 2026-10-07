import { useState } from 'react'
import Header, { type Page } from './components/Header'
import NewDeploy from './pages/NewDeploy'
import History from './pages/History'
import Targets from './pages/Targets'

export default function App() {
  const [page, setPage] = useState<Page>('new')

  return (
    <>
      <Header page={page} onChange={setPage} />
      <main className="container">
        {page === 'new' && <NewDeploy onShowHistory={() => setPage('history')} />}
        {page === 'history' && <History />}
        {page === 'targets' && <Targets />}
      </main>
    </>
  )
}

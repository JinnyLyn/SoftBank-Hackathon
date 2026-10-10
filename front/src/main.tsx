import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import OnPremDeploy from './onprem/OnPremDeploy'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {window.location.pathname === '/onprem' ? <OnPremDeploy /> : <App />}
  </StrictMode>,
)

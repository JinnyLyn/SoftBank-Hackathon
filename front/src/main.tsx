import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import OnPremDeploy from './onprem/OnPremDeploy'
import './styles.css'
import { getLang, registerDictionary } from './i18n/core'
import ja from './i18n/ja'

registerDictionary('ja', ja)
document.documentElement.lang = getLang()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {window.location.pathname === '/onprem' ? <OnPremDeploy /> : <App />}
  </StrictMode>,
)

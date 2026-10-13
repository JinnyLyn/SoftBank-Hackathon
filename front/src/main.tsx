import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import OnPremDeploy from './onprem/OnPremDeploy'
import OnPremMock from './onprem/OnPremMock'
import './styles.css'
import { getLang, registerDictionary } from './i18n/core'
import ja from './i18n/ja'

registerDictionary('ja', ja)
document.documentElement.lang = getLang()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {window.location.pathname === '/onprem'
      ? import.meta.env.VITE_ONPREM_ONLY === 'true' ? <OnPremDeploy /> : <OnPremMock />
      : <App />}
  </StrictMode>,
)

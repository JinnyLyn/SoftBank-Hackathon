import { PROVIDERS } from '../providers'
import type { Provider } from '../types'

// 벤더 로고 대신 모두 같은 모양의 글자 표시
export default function ProviderMark({ provider }: { provider: Provider }) {
  return (
    <span className="pmark" title={PROVIDERS[provider].label}>
      {PROVIDERS[provider].mark}
    </span>
  )
}

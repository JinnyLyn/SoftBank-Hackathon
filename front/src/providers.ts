import type { Provider } from './types'

export interface ProviderMeta {
  label: string
  /** 목록에 붙는 짧은 표시 */
  mark: string
  kind: 'cloud' | 'server'
}

export const PROVIDERS: Record<Provider, ProviderMeta> = {
  aws: {
    label: 'AWS',
    mark: 'AWS',
    kind: 'cloud',
  },
  onprem: {
    label: '온프레미스',
    mark: 'SRV',
    kind: 'server',
  },
}

const ALL_PROVIDERS: Provider[] = ['aws', 'onprem']

/**
 * 화면에 보일 배포 대상 종류. `.env` 의 VITE_PROVIDERS=aws,onprem 처럼 지정.
 * 지정하지 않으면 AWS만. onprem은 worker를 `--targets aws,local_docker`로 실행했을 때(발표자 PC의 로컬 Docker 배포) 추가
 */
export const ENABLED_PROVIDERS: Provider[] = (() => {
  const raw = String(import.meta.env.VITE_PROVIDERS ?? 'aws')
  const picked = ALL_PROVIDERS.filter((p) => raw.split(',').map((s) => s.trim()).includes(p))
  return picked.length ? picked : ['aws']
})()

export const isEnabled = (p: Provider) => ENABLED_PROVIDERS.includes(p)
export const ONPREM_ENABLED = isEnabled('onprem')

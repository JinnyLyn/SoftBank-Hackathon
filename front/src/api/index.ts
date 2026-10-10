import * as mock from './mock'
import * as backend from './real'

export { sourceName } from '../format'
export { ApiError } from './http'

// 기본은 실제 백엔드. 백엔드 없이 화면만 볼 때 VITE_USE_MOCK=true
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 화면에 MOCK 표시를 띄울지. 모의 결과를 실제 배포로 오해하지 않게 */
export const IS_MOCK = USE_MOCK

/**
 * 신규 도메인 구매를 화면에 보일지. 서버·인프라가 구매(등록인 정보·동의·결제·등록)를 지원할 때만 켬.
 * 지원하지 않는데 구매 가능처럼 보이지 않게 함 (docs/PRODUCT_DIRECTION.md §3 프런트). mock은 시제품을 보여 주려고 켬
 */
export const DOMAIN_PURCHASE = USE_MOCK || import.meta.env.VITE_DOMAIN_PURCHASE === 'true'

// 화면은 api.* 만 부름. mock과 실제 백엔드(real.ts, back/API.md 기준)가 같은 모양을 지켜야 함
type Api = Pick<
  typeof backend,
  | 'listConnections'
  | 'saveConnection'
  | 'checkConnection'
  | 'deleteConnection'
  | 'analyze'
  | 'recommend'
  | 'generate'
  | 'approve'
  | 'applyFix'
  | 'status'
  | 'history'
  | 'checkDomain'
  | 'saveDomain'
  | 'confirmDomainPurchase'
>

export const api: Api = USE_MOCK ? mock : backend

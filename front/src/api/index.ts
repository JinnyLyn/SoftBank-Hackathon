import * as mock from './mock'
import * as backend from './real'

export { sourceName } from '../format'
export { ApiError } from './http'

// 기본은 실제 백엔드. 백엔드 없이 화면만 볼 때 VITE_USE_MOCK=true
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 화면에 MOCK 표시를 띄울지. 모의 결과를 실제 배포로 오해하지 않게 */
export const IS_MOCK = USE_MOCK

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
>

export const api: Api = USE_MOCK ? mock : backend

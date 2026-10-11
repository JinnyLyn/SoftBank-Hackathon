import * as mock from './mock'
import * as backend from './real'

export { sourceName } from '../format'
export { ApiError } from './http'

// 기본은 실제 백엔드. 백엔드 없이 화면만 볼 때 VITE_USE_MOCK=true
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 화면에 MOCK 표시를 띄울지. 모의 결과를 실제 배포로 오해하지 않게 */
export const IS_MOCK = USE_MOCK

/**
 * worker가 Dockerfile 초안으로 빌드하는지 (승인된 초안을 docker build -f 로 사용). 기본은 켬.
 * 켜면 초안 앱도 바로 계획·비용 승인으로 이어지고, false 면 초안을 저장소에 넣고 다시 올리도록 안내 (초안 빌드가 없는 예전 worker용)
 */
export const DRAFT_BUILD = import.meta.env.VITE_DOCKERFILE_DRAFT_BUILD !== 'false'

// 화면은 api.* 만 부름. mock과 실제 백엔드(real.ts, back/API.md 기준)가 같은 모양을 지켜야 함
type Api = Pick<
  typeof backend,
  | 'analyze'
  | 'recommend'
  | 'generate'
  | 'approve'
  | 'applyFix'
  | 'status'
  | 'history'
  | 'saveDomain'
  | 'requestTier'
>

export const api: Api = USE_MOCK ? mock : backend

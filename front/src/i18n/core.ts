/**
 * 화면 언어(한국어·일본어). 라이브러리 없이 한국어 원문을 키로 쓴다: t('새 배포') → 일본어 사전에 있으면 일본어, 없으면 원문.
 * 값이 들어가는 문장은 {이름} 자리표시를 쓴다: t('월 예산 {budget} 안', { budget: usd(n) }).
 * 이 파일은 React·사전을 가져오지 않는다(테스트가 그대로 읽을 수 있게). 사전은 main.tsx 에서 registerDictionary 로 넣는다.
 * 서버·worker·분석기가 만든 문장(계획 이유, 오류 원인 등)은 번역하지 않고 그대로 보여 준다.
 */
export type Lang = 'ko' | 'ja'

export const LANGS: { value: Lang; label: string }[] = [
  { value: 'ko', label: '한국어' },
  { value: 'ja', label: '日本語' },
]

const STORAGE_KEY = 'paved-lang'
const dictionaries: Partial<Record<Lang, Record<string, string>>> = {}
const listeners = new Set<() => void>()

function initialLang(): Lang {
  try {
    const saved = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (saved === 'ko' || saved === 'ja') return saved
  } catch {
    // 저장소를 못 쓰는 환경(사생활 보호 모드 등)이면 기본값
  }
  return 'ko'
}

let current: Lang = initialLang()

export function registerDictionary(lang: Lang, dict: Record<string, string>) {
  dictionaries[lang] = { ...dictionaries[lang], ...dict }
}

export const getLang = () => current

export function setLang(lang: Lang) {
  if (lang === current) return
  current = lang
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, lang)
  } catch {
    // 저장하지 못해도 이번 화면에서는 바뀜
  }
  if (typeof document !== 'undefined') document.documentElement.lang = lang
  listeners.forEach((fn) => fn())
}

export function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** 번역. 사전에 없으면 한국어 원문. vars 는 {이름} 자리에 채움 */
export function t(source: string, vars?: Record<string, string | number>): string {
  const text = (current !== 'ko' && dictionaries[current]?.[source]) || source
  return vars ? text.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : text
}

import { useSyncExternalStore } from 'react'
import { getLang, LANGS, setLang, subscribe, t, type Lang } from './core'

export { getLang, LANGS, setLang, t, type Lang } from './core'

/** 언어가 바뀌면 이 훅을 쓰는 컴포넌트가 다시 그려짐. 화면 문구는 이 훅이 돌려준 t 로 */
export function useT() {
  useSyncExternalStore(subscribe, getLang, getLang)
  return t
}

export function useLang(): Lang {
  return useSyncExternalStore(subscribe, getLang, getLang)
}

/** 오른쪽 위 언어 스위치 (한국어 / 日本語) */
export function LangSwitch({ className = '' }: { className?: string }) {
  const lang = useLang()
  return (
    <div className={'lang-switch ' + className} role="group" aria-label="Language">
      {LANGS.map((l) => (
        <button key={l.value} type="button" className={lang === l.value ? 'is-on' : ''} aria-pressed={lang === l.value}
          onClick={() => setLang(l.value)}>
          {l.label}
        </button>
      ))}
    </div>
  )
}

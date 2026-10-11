import { useState } from 'react'
import CodeView from './CodeView'
import { DRAFT_BUILD } from '../api'
import { useT } from '../i18n'
import type { DockerfileDraft as Draft } from '../types'

/** Dockerfile이 없는 앱에 분석기가 만든 초안. 사용자가 저장소에 넣고 다시 올리는 제안 (배포에 바로 쓰지 않음) */
export default function DockerfileDraft({ draft }: { draft: Draft }) {
  const t = useT()
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(draft.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // 권한이 없으면 사용자가 직접 선택해서 복사
    }
  }

  const download = () => {
    const url = URL.createObjectURL(new Blob([draft.content], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'Dockerfile'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section className="draft-box">
      {DRAFT_BUILD ? (
        <div className="draft-next" role="status">
          <strong>{t('이 Dockerfile 초안으로 이미지를 만듭니다')}</strong>
          <span>
            {t(
              '저장소에는 Dockerfile이 없어 코드를 보고 초안을 만들었습니다(포트 {port}). 비용 승인 화면의 코드 탭에서 초안을 다시 확인하고, 승인하면 이 내용 그대로 빌드합니다. 저장소에 넣어 두고 싶으면 다운로드하세요.',
              { port: draft.port },
            )}
          </span>
        </div>
      ) : (
        <div className="draft-next" role="status">
          <strong>{t('Dockerfile만 넣으면 배포할 수 있습니다')}</strong>
          <ol>
            <li>{t('아래 초안을 확인하고 다운로드하세요. 코드를 보고 만든 것이라 필요하면 고쳐서 쓰세요.')}</li>
            <li>{t('저장소 루트(맨 위 폴더)에 Dockerfile 이라는 이름으로 추가하세요.')}</li>
            <li>{t('처음 화면에서 "새 배포"로 다시 올리면 구성·비용 계획으로 이어집니다. 포트는 초안의 {port}번을 씁니다.', { port: draft.port })}</li>
          </ol>
        </div>
      )}
      <div className="draft-head">
        <div>
          <h3 className="sub-title">{t('Dockerfile 초안')}</h3>
          {!DRAFT_BUILD && (
            <p className="hint">{t('플랫폼은 저장소 안의 Dockerfile로 이미지를 만들기 때문에, 초안을 저장소에 넣어야 배포에 쓰입니다.')}</p>
          )}
        </div>
        <div className="draft-actions">
          <button className="btn btn-ghost btn-sm" onClick={copy}>
            {copied ? t('복사됨') : t('복사')}
          </button>
          <button className="btn btn-sm" onClick={download}>
            {t('다운로드')}
          </button>
        </div>
      </div>
      <div className="code-box">
        <CodeView kind="hcl" text={draft.content.replace(/\n$/, '').split('\n')} numbered />
      </div>
      {draft.basedOn.length > 0 && <p className="muted small">{t('근거:')} {draft.basedOn.join(' · ')}</p>}
    </section>
  )
}

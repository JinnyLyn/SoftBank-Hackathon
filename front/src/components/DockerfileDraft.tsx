import { useState } from 'react'
import CodeView from './CodeView'
import type { DockerfileDraft as Draft } from '../types'

/** Dockerfile이 없는 앱에 분석기가 만든 초안. 사용자가 저장소에 넣고 다시 올리는 제안 (배포에 바로 쓰지 않음) */
export default function DockerfileDraft({ draft }: { draft: Draft }) {
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
      <div className="draft-head">
        <div>
          <h3 className="sub-title">Dockerfile 초안</h3>
          <p className="hint">
            저장소에 Dockerfile이 없어 코드를 보고 초안을 만들었습니다(포트 {draft.port}). 내용을 확인하고 저장소 루트에{' '}
            <code>Dockerfile</code> 로 추가한 뒤 처음 화면에서 다시 올리면 배포할 수 있습니다.
          </p>
        </div>
        <div className="draft-actions">
          <button className="btn btn-ghost btn-sm" onClick={copy}>
            {copied ? '복사됨' : '복사'}
          </button>
          <button className="btn btn-sm" onClick={download}>
            다운로드
          </button>
        </div>
      </div>
      <div className="code-box">
        <CodeView kind="hcl" text={draft.content.replace(/\n$/, '').split('\n')} numbered />
      </div>
      {draft.basedOn.length > 0 && <p className="muted small">근거: {draft.basedOn.join(' · ')}</p>}
    </section>
  )
}

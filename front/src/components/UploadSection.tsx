import { useRef, useState, type DragEvent } from 'react'
import Section from './Section'
import type { ExpectedUsers, ScaleInput } from '../types'

const USER_OPTIONS: ExpectedUsers[] = ['~100', '~1,000', '~10,000', '10,000+']

interface Props {
  file: File | null
  scale: ScaleInput
  analyzing: boolean
  analyzed: boolean
  locked: boolean
  onFile: (f: File) => void
  onScale: (s: ScaleInput) => void
  onAnalyze: () => void
}

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

export default function UploadSection(p: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const pick = () => input.current?.click()

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f && f.name.toLowerCase().endsWith('.zip')) p.onFile(f)
  }

  let fileNote = '압축 해제 전'
  if (p.analyzing) fileNote = '압축 해제하고 파일 스캔 중'
  else if (p.analyzed) fileNote = '업로드 완료, 압축 해제 후 파일 스캔 끝남'
  else if (p.file) fileNote = `${formatSize(p.file.size)}, 분석 대기`

  return (
    <Section no={1} title="웹앱 업로드">
      <input
        ref={input}
        type="file"
        accept=".zip"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) p.onFile(f)
          e.target.value = ''
        }}
      />

      {p.file ? (
        <div className="file-row">
          <svg className="file-icon" width="22" height="22" viewBox="0 0 24 24" aria-hidden>
            <path d="M12 2.5 3.5 7v10l8.5 4.5 8.5-4.5V7z M3.5 7 12 11.5 20.5 7 M12 11.5v10" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
          </svg>
          <div className="file-meta">
            <strong>{p.file.name}</strong>
            <span>{fileNote}</span>
          </div>
          <button className="btn" onClick={pick} disabled={p.locked || p.analyzing}>
            다른 파일 선택
          </button>
        </div>
      ) : (
        <div
          className={'dropzone' + (dragging ? ' is-over' : '')}
          onClick={pick}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === 'Enter' && pick()}
        >
          <strong>프로젝트 zip 파일을 끌어다 놓거나 클릭해서 선택</strong>
          <span>소스 코드 전체를 압축한 파일. node_modules, .venv 는 빼고 올려 주세요.</span>
        </div>
      )}

      {p.file && !p.analyzed && (
        <div className="scale-form">
          <div className="field">
            <label>예상 사용자 수 (월)</label>
            <div className="segmented">
              {USER_OPTIONS.map((o) => (
                <button
                  key={o}
                  className={p.scale.expectedUsers === o ? 'is-on' : ''}
                  onClick={() => p.onScale({ ...p.scale, expectedUsers: o })}
                  disabled={p.analyzing}
                >
                  {o}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label htmlFor="purpose">서비스 설명 (선택)</label>
            <input
              id="purpose"
              className="input"
              placeholder="예: 동아리 출석 체크용, 평일 저녁에만 씀"
              value={p.scale.purpose}
              onChange={(e) => p.onScale({ ...p.scale, purpose: e.target.value })}
              disabled={p.analyzing}
            />
          </div>
          <div className="form-actions">
            <span className="hint">입력값은 인스턴스 크기와 비용 계산에만 씁니다.</span>
            <button className="btn btn-primary" onClick={p.onAnalyze} disabled={p.analyzing}>
              {p.analyzing ? '분석 중…' : '분석 시작'}
            </button>
          </div>
        </div>
      )}
    </Section>
  )
}

import { useRef, useState, type DragEvent } from 'react'
import type { Source } from '../types'

const MAX_ZIP_BYTES = 200 * 1024 * 1024
const GITHUB_RE = /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+?(\.git)?\/?$/

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

interface Props {
  source: Source | null
  locked: boolean
  onSource: (s: Source | null) => void
}

export default function SourceStep({ source, locked, onSource }: Props) {
  const [mode, setMode] = useState<Source['kind']>(source?.kind ?? 'zip')
  const [url, setUrl] = useState(source?.kind === 'github' ? source.url : '')
  const [branch, setBranch] = useState(source?.kind === 'github' ? source.branch : 'main')
  const [dragging, setDragging] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  // 형식과 크기는 서버에서도 다시 검사함. 여기서는 빨리 알려 주는 용도
  const takeFile = (f: File | undefined) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.zip')) return setFileError('zip 파일만 올릴 수 있습니다.')
    if (f.size > MAX_ZIP_BYTES) return setFileError(`200MB를 넘습니다 (${formatSize(f.size)}). node_modules 같은 폴더를 빼고 다시 압축해 주세요.`)
    setFileError(null)
    onSource({ kind: 'zip', file: f })
  }

  const urlValid = GITHUB_RE.test(url.trim())
  const pick = () => input.current?.click()

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    takeFile(e.dataTransfer.files[0])
  }

  if (source) {
    return (
      <div className="stack">
        <div className="source-card">
          <div className="source-kind">{source.kind === 'zip' ? 'ZIP' : 'GitHub'}</div>
          <div className="source-meta">
            {source.kind === 'zip' ? (
              <>
                <strong>{source.file.name}</strong>
                <span>{formatSize(source.file.size)}</span>
              </>
            ) : (
              <>
                <strong className="mono">{source.url.replace('https://github.com/', '')}</strong>
                <span>브랜치 {source.branch}</span>
              </>
            )}
          </div>
          {!locked && (
            <button className="btn btn-ghost" onClick={() => onSource(null)}>
              바꾸기
            </button>
          )}
        </div>
        <Checklist />
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="switch" role="tablist">
        <button role="tab" aria-selected={mode === 'zip'} className={mode === 'zip' ? 'is-on' : ''} onClick={() => setMode('zip')}>
          zip 파일
        </button>
        <button role="tab" aria-selected={mode === 'github'} className={mode === 'github' ? 'is-on' : ''} onClick={() => setMode('github')}>
          GitHub 저장소
        </button>
      </div>

      {mode === 'zip' ? (
        <>
          <input
            ref={input}
            type="file"
            accept=".zip"
            hidden
            onChange={(e) => {
              takeFile(e.target.files?.[0])
              e.target.value = ''
            }}
          />
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
            <strong>zip 파일을 여기에 놓기</strong>
            <span>또는 클릭해서 선택 · 최대 200MB</span>
          </div>
          {fileError && <p className="conn-error">{fileError}</p>}
        </>
      ) : (
        <form
          className="repo-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (urlValid) onSource({ kind: 'github', url: url.trim(), branch: branch.trim() || 'main' })
          }}
        >
          <div className="field grow">
            <label htmlFor="repo">저장소 주소</label>
            <input
              id="repo"
              className="input mono"
              placeholder="https://github.com/owner/repo"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="field branch">
            <label htmlFor="branch">브랜치</label>
            <input id="branch" className="input mono" value={branch} onChange={(e) => setBranch(e.target.value)} />
          </div>
          <button className="btn" type="submit" disabled={!urlValid}>
            가져오기
          </button>
          <p className="hint full">
            {url && !urlValid ? 'github.com/소유자/저장소 형식의 주소를 넣어 주세요.' : 'public 저장소만 가져올 수 있습니다.'}
          </p>
        </form>
      )}

      <Checklist />
    </div>
  )
}

function Checklist() {
  return (
    <div className="checklist">
      <h3>같이 들어 있으면 분석이 정확해집니다</h3>
      <ul>
        <li>
          <code>requirements.txt</code>, <code>package.json</code> 같은 의존성 파일
        </li>
        <li>
          <code>.env.example</code> — 실제 값 말고 변수 이름만
        </li>
        <li>
          <code>Dockerfile</code> — 없으면 새로 만들어 드립니다
        </li>
      </ul>
      <p className="hint">node_modules, .venv, .env 는 빼고 올려 주세요.</p>
    </div>
  )
}

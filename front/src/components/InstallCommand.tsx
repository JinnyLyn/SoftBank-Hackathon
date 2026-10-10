import { useEffect, useRef, useState } from 'react'
import { INSTALL_SCRIPT_PREVIEW } from '../providers'
import CodeView from './CodeView'
import type { Connection } from '../types'

const POLL_MS = 3000

interface Props {
  conn: Connection
  /** 서버가 보고했는지 확인. 연결되면 connected로 바뀐 값을 돌려줌 */
  onCheck: () => Promise<Connection>
  /** 만료됐을 때 새 명령 받기 */
  onReissue: () => Promise<void>
  onClose: () => void
}

function remaining(expiresAt?: string) {
  if (!expiresAt) return 0
  return Math.max(0, new Date(expiresAt).getTime() - Date.now())
}

export default function InstallCommand({ conn, onCheck, onReissue, onClose }: Props) {
  const [left, setLeft] = useState(() => remaining(conn.expiresAt))
  const [copied, setCopied] = useState(false)
  const [reissuing, setReissuing] = useState(false)
  const check = useRef(onCheck)
  check.current = onCheck

  const connected = conn.status === 'connected'
  const expired = !connected && left === 0

  // 남은 시간
  useEffect(() => {
    setLeft(remaining(conn.expiresAt))
    if (connected) return
    const t = window.setInterval(() => setLeft(remaining(conn.expiresAt)), 1000)
    return () => window.clearInterval(t)
  }, [conn.expiresAt, connected])

  // 서버가 스크립트를 실행해 보고할 때까지 주기적으로 확인
  useEffect(() => {
    if (connected || expired) return
    let stopped = false
    let timer: number
    const tick = async () => {
      try {
        const c = await check.current()
        if (stopped || c.status === 'connected') return
      } catch {
        // 일시적인 오류는 다음 확인 때 다시 시도
      }
      if (!stopped) timer = window.setTimeout(tick, POLL_MS)
    }
    timer = window.setTimeout(tick, POLL_MS)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [connected, expired, conn.id])

  const copy = async () => {
    if (!conn.installCommand) return
    try {
      await navigator.clipboard.writeText(conn.installCommand)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // 클립보드 권한이 없으면 사용자가 직접 선택해서 복사
    }
  }

  const reissue = async () => {
    setReissuing(true)
    try {
      await onReissue()
    } finally {
      setReissuing(false)
    }
  }

  if (connected) {
    return (
      <div className="install stack">
        <div className="install-done">
          <span className="done-label">연결됨</span>
          <strong>{conn.name}</strong>
          <span className="mono small">{conn.detail}</span>
        </div>
        <p className="hint">Docker 설치, 배포 전용 사용자, 접속 키 등록까지 끝났습니다. 이제 새 배포에서 이 서버도 함께 비교합니다.</p>
        <button className="btn" onClick={onClose}>
          다른 대상 추가
        </button>
      </div>
    )
  }

  const mm = Math.floor(left / 60000)
  const ss = String(Math.floor((left % 60000) / 1000)).padStart(2, '0')

  return (
    <div className="install stack">
      <ol className="install-steps">
        <li>
          <strong>서버 터미널을 엽니다</strong>
          <span>SSH로 접속하거나 서버 앞에서 직접 엽니다. Ubuntu, Debian, Rocky Linux를 지원합니다.</span>
        </li>
        <li>
          <strong>아래 명령 한 줄을 붙여 넣고 실행합니다</strong>
          <span>Docker 설치부터 접속 설정까지 알아서 합니다. 비밀번호를 물으면 서버 관리자 비밀번호를 넣으세요.</span>
        </li>
      </ol>

      <div className={'cmd' + (expired ? ' is-expired' : '')}>
        <code>{conn.installCommand}</code>
        <button className="cmd-copy" onClick={copy} disabled={expired}>
          {copied ? '복사됨' : '복사'}
        </button>
      </div>

      {expired ? (
        <div className="cmd-meta">
          <span className="conn-error">명령이 만료되었습니다.</span>
          <button className="link-btn" onClick={reissue} disabled={reissuing}>
            {reissuing ? '만드는 중…' : '새 명령 만들기'}
          </button>
        </div>
      ) : (
        <div className="cmd-meta">
          <span className="waiting">
            <span className="pulse" /> 서버에서 실행하기를 기다리는 중
          </span>
          <span className="muted small mono">
            {mm}:{ss} 후 만료
          </span>
        </div>
      )}

      <details className="evidence">
        <summary>스크립트가 하는 일 보기</summary>
        <CodeView kind="hcl" text={INSTALL_SCRIPT_PREVIEW} />
      </details>

      <p className="hint">
        명령 안의 토큰은 이 서버 한 대를 등록하는 데 한 번만 쓰이고 10분 뒤 만료됩니다. 다른 사람과 공유하지 마세요.
      </p>
    </div>
  )
}

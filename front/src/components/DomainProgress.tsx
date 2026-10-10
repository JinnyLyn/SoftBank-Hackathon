import { useState } from 'react'
import type { DnsRecord, DomainMode, DomainPlan, DomainStatus } from '../types'

// 단계 순서. auto는 플랫폼이 DNS를 직접 넣으므로 사용자가 할 일이 없음
const ORDER: Record<DomainMode, { state: DomainStatus['state']; label: string }[]> = {
  auto: [
    { state: 'waiting_dns', label: '주소 연결 중' },
    { state: 'issuing_cert', label: 'HTTPS 준비 중' },
    { state: 'active', label: '연결 완료' },
  ],
  own: [
    { state: 'waiting_dns', label: 'DNS 확인 중' },
    { state: 'issuing_cert', label: '인증서 발급 중' },
    { state: 'active', label: '도메인 연결 완료' },
  ],
}

interface Props {
  status: DomainStatus
  /** 사용자가 고른 방식. 서버 상태만으로는 auto/own을 알 수 없어서 받음 */
  mode: DomainPlan['mode']
}

export default function DomainProgress({ status, mode }: Props) {
  if (mode === 'none' || status.state === 'skipped') {
    return (
      <div className="domain-progress">
        <div className="domain-head">
          <strong>도메인 미연결 · 미리보기</strong>
          <span className="muted small">{status.message ?? '독립 도메인 없이 미리보기 주소(AWS 기본 주소)로 접속합니다. 도메인 연결 완료가 아닙니다.'}</span>
        </div>
      </div>
    )
  }

  const steps = ORDER[mode]
  const current = steps.findIndex((s) => s.state === status.state)
  const failed = status.state === 'failed'

  return (
    <div className="domain-progress">
      <div className="domain-head">
        <strong>{mode === 'auto' ? '주소' : '도메인'} {status.name}</strong>
        {status.state === 'active' && status.url ? (
          <a href={status.url} target="_blank" rel="noreferrer" className="mono">
            {status.url}
          </a>
        ) : (
          <span className={failed ? 'conn-error' : 'muted small'}>{status.message}</span>
        )}
      </div>
      <ol className="domain-steps">
        {steps.map((s, i) => {
          const state = failed ? (i <= Math.max(current, 0) ? 'is-failed' : '') : i < current || status.state === 'active' ? 'is-done' : i === current ? 'is-current' : ''
          return (
            <li key={s.state} className={state}>
              <span className="dot" aria-hidden />
              {s.label}
              {state === 'is-current' && <span className="pulse" aria-hidden />}
            </li>
          )
        })}
      </ol>
      {status.state === 'waiting_dns' && status.records && status.records.length > 0 && <RecordTable records={status.records} />}
    </div>
  )
}

function RecordTable({ records }: { records: DnsRecord[] }) {
  const [copied, setCopied] = useState<string | null>(null)
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(text)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      // 권한이 없으면 사용자가 직접 선택해서 복사
    }
  }
  return (
    <div className="record-box">
      <p className="hint">도메인을 산 곳(가비아, Cloudflare 등)의 DNS 설정에 아래 레코드를 추가해 주세요. 반영까지 몇 분에서 몇 시간 걸릴 수 있습니다.</p>
      <table className="table">
        <thead>
          <tr>
            <th>종류</th>
            <th>이름</th>
            <th>값</th>
            <th>용도</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <tr key={r.type + r.name}>
              <td className="mono">{r.type}</td>
              <td className="mono small">
                {r.name}{' '}
                <button className="link-btn" onClick={() => copy(r.name)}>
                  {copied === r.name ? '복사됨' : '복사'}
                </button>
              </td>
              <td className="mono small record-value">
                {r.value}{' '}
                <button className="link-btn" onClick={() => copy(r.value)}>
                  {copied === r.value ? '복사됨' : '복사'}
                </button>
              </td>
              <td className="muted small">{r.purpose}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export const domainDone = (s?: DomainStatus) => !s || ['active', 'failed', 'skipped'].includes(s.state)

import { useState } from 'react'
import { isEnabled } from '../providers'
import { useT } from '../i18n'
import type { DnsRecord, DomainMode, DomainPlan, DomainStatus } from '../types'

/** 도메인 없이 접속하는 미리보기 주소가 어디 것인지. 켜 둔 배포 대상을 따름 */
const PREVIEW_ADDRESS = isEnabled('aws') ? (isEnabled('onprem') ? 'AWS 기본 주소 또는 이 PC의 로컬 주소' : 'AWS 기본 주소') : '이 PC의 로컬 주소'

// 단계 순서. auto는 플랫폼이 DNS를 직접 넣으므로 사용자가 할 일이 없음
const ORDER: Record<DomainMode, { state: DomainStatus['state']; label: string }[]> = {
  auto: [
    { state: 'waiting_dns', label: '주소 연결 중' },
    { state: 'issuing_cert', label: 'HTTPS 준비 중' },
    { state: 'active', label: '플랫폼 주소 연결 완료' },
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
  const t = useT()
  if (mode === 'none' || status.state === 'skipped') {
    return (
      <div className="domain-progress">
        <div className="domain-head">
          <strong>{t('도메인 미연결 · 미리보기')}</strong>
          <span className="muted small">{status.message ?? t('독립 도메인 없이 미리보기 주소({address})로 접속합니다. 도메인 연결 완료가 아닙니다.', { address: t(PREVIEW_ADDRESS) })}</span>
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
        <strong>{mode === 'auto' ? t('플랫폼 주소') : t('도메인')} {status.name}</strong>
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
              {t(s.label)}
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
  const t = useT()
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
      <p className="hint">{t('도메인을 산 곳(가비아, Cloudflare 등)의 DNS 설정에 아래 레코드를 추가해 주세요. 반영까지 몇 분에서 몇 시간 걸릴 수 있습니다.')}</p>
      <table className="table">
        <thead>
          <tr>
            <th>{t('종류')}</th>
            <th>{t('이름')}</th>
            <th>{t('값')}</th>
            <th>{t('용도')}</th>
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <tr key={r.type + r.name}>
              <td className="mono">{r.type}</td>
              <td className="mono small">
                {r.name}{' '}
                <button className="link-btn" onClick={() => copy(r.name)}>
                  {copied === r.name ? t('복사됨') : t('복사')}
                </button>
              </td>
              <td className="mono small record-value">
                {r.value}{' '}
                <button className="link-btn" onClick={() => copy(r.value)}>
                  {copied === r.value ? t('복사됨') : t('복사')}
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

/**
 * 도메인 확인을 멈춰도 되는지. 도메인 기능이 없어 미리보기로 진행한 경우(mode none)만 상태가 없어도 끝.
 * 주소를 고른 배포는 상태가 올 때까지 기다리고, active·failed·skipped 에서 멈춤 (PR #25 리뷰: 상태가 늦게 오면 완료로 처리했음)
 */
export const domainDone = (s: DomainStatus | undefined, mode: DomainPlan['mode']) =>
  mode === 'none' ? true : !!s && ['active', 'failed', 'skipped'].includes(s.state)

/** 연결에 실패했는지 (진행 단계 표시용) */
export const domainFailed = (s?: DomainStatus) => s?.state === 'failed'

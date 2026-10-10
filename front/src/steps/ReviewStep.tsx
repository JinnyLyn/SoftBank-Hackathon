import { useState } from 'react'
import CodeView from '../components/CodeView'
import ProviderMark from '../components/ProviderMark'
import { PROVIDERS } from '../providers'
import type { DomainPlan, Provider, TerraformBundle, Tier } from '../types'
import { costText, countText, tierTotal, usd } from '../format'

interface Props {
  bundle: TerraformBundle
  tier: Tier
  target: { name: string; provider: Provider }
  /** 도메인 계획. null 이거나 mode none 이면 미리보기 주소 */
  domain: DomainPlan | null
  confirmed: boolean
  locked: boolean
  onConfirm: (v: boolean) => void
}

export default function ReviewStep({
  bundle,
  tier,
  target,
  domain,
  confirmed,
  locked,
  onConfirm,
}: Props) {
  const [tab, setTab] = useState('plan')
  const file = bundle.files.find((f) => f.name === tab)
  const { plan } = bundle
  const cost = tierTotal(tier)
  const isServer = PROVIDERS[target.provider].kind === 'server'
  const planLabel = bundle.tool === 'terraform' ? 'terraform plan' : 'compose 변경 사항'

  const download = () => {
    const text = bundle.files.map((f) => `# ===== ${f.name} =====\n${f.content}\n`).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    a.download = bundle.tool === 'terraform' ? 'terraform.tf.txt' : 'compose.txt'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="stack">
      {bundle.patches && bundle.patches.length > 0 && (
        <div className="refix">
          <strong>실패 진단의 수정안을 반영해 다시 만든 계획입니다. 바뀐 내용을 확인하고 다시 승인해 주세요.</strong>
          {bundle.patches.map((p) => (
            <div key={p.file} className="diff">
              <div className="diff-file mono">{p.file}</div>
              {p.before.map((l) => (
                <div key={'b' + l} className="diff-del mono">- {l}</div>
              ))}
              {p.after.map((l) => (
                <div key={'a' + l} className="diff-add mono">+ {l}</div>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="summary-strip">
        <div>
          <span>추가</span>
          <strong className="c-add">{countText(plan.add)}</strong>
        </div>
        <div>
          <span>변경</span>
          <strong>{countText(plan.change)}</strong>
        </div>
        <div>
          <span>삭제</span>
          <strong className={plan.destroy ? 'c-del' : ''}>{countText(plan.destroy)}</strong>
        </div>
        <div>
          <span>대상</span>
          <strong className="with-mark">
            <ProviderMark provider={target.provider} /> {target.name}
          </strong>
        </div>
        <div>
          <span>구성</span>
          <strong>{tier.label}</strong>
        </div>
        <div>
          <span>앱 월 예상</span>
          <strong>{costText(cost)}</strong>
        </div>
        <div>
          <span>도메인</span>
          <strong>{domainSummary(domain)}</strong>
        </div>
      </div>

      <table className="table cost-breakdown">
        <thead>
          <tr>
            <th>항목</th>
            <th>내용</th>
            <th className="num">매달</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>앱 ({tier.label})</td>
            <td className="muted">{target.name}</td>
            <td className="num">{costText(cost)}</td>
          </tr>
          <tr>
            <td>주소</td>
            <td className="muted mono">{domainDetail(domain)}</td>
            <td className="num">{domain && domain.monthlyUsd > 0 ? usd(domain.monthlyUsd) : '-'}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={2}>합계</td>
            <td className="num">{usd(cost + (domain?.monthlyUsd ?? 0))}</td>
          </tr>
        </tfoot>
      </table>
      {domain?.note && <p className="hint">{domain.note}</p>}

      {bundle.planInfo && (
        <dl className="plan-info">
          <div>
            <dt>모듈</dt>
            <dd className="mono">{bundle.planInfo.moduleId}</dd>
          </div>
          {bundle.planInfo.pricingAsOf && (
            <div>
              <dt>가격 기준</dt>
              <dd>{bundle.planInfo.pricingAsOf}</dd>
            </div>
          )}
          <div>
            <dt>승인 식별자</dt>
            <dd className="mono" title={bundle.planInfo.fingerprint}>
              {bundle.planInfo.fingerprint.slice(0, 12)}…
            </dd>
          </div>
          <div>
            <dt>Terraform plan 파일</dt>
            <dd className={bundle.planInfo.ready ? 'c-add' : 'c-del'}>{bundle.planInfo.ready ? '준비됨' : '아직 없음'}</dd>
          </div>
        </dl>
      )}
      {bundle.planInfo && !bundle.planInfo.ready && (
        <p className="conn-error">
          서버에 Terraform plan 파일이 아직 올라오지 않아 승인할 수 없습니다. 몇 초마다 다시 확인해서 준비되면 바로 승인할 수 있게 바뀝니다.
        </p>
      )}

      <div className="code-box">
        <div className="code-tabs">
          {['plan', ...bundle.files.map((f) => f.name)].map((name) => (
            <button key={name} className={tab === name ? 'is-on' : ''} onClick={() => setTab(name)}>
              {name === 'plan' ? planLabel : name}
            </button>
          ))}
          <button className="code-dl" onClick={download}>
            코드 내려받기
          </button>
        </div>
        {file ? <CodeView kind="hcl" text={file.content} numbered /> : <CodeView kind="plan" text={plan.text} />}
      </div>

      <label className={'confirm' + (confirmed ? ' is-on' : '')}>
        <input
          type="checkbox"
          checked={confirmed}
          disabled={locked || bundle.planInfo?.ready === false}
          onChange={(e) => onConfirm(e.target.checked)}
        />
        <span>
          {isServer
            ? `${target.name}에 띄울 컨테이너 ${countText(plan.add)}개를 확인했습니다.`
            : plan.add === null
              ? `배포 계획과 월 예상 비용 ${costText(cost)}을 확인했습니다.`
              : `만들어질 리소스 ${plan.add}개와 월 예상 비용 ${costText(cost)}을 확인했습니다.`}
          <small>
            {isServer
              ? '승인하면 SSH로 서버에 접속해 docker compose up 을 실행합니다.'
              : `승인하면 ${target.name}에 실제로 리소스가 생기고 비용이 나가기 시작합니다.`}
          </small>
        </span>
      </label>

    </div>
  )
}

function domainSummary(domain: DomainPlan | null) {
  if (!domain || domain.mode === 'none') return '미리보기 주소'
  return domain.mode === 'auto' ? '자동 주소' : '보유 도메인'
}

function domainDetail(domain: DomainPlan | null) {
  if (!domain || domain.mode === 'none') return 'AWS 기본 주소 (미리보기, 도메인 미연결)'
  return `${domain.name ?? '배포 뒤 확정'} (${domain.mode === 'auto' ? '자동 주소' : '가지고 있는 도메인 연결'})`
}

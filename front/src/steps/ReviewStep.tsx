import { useState } from 'react'
import CodeView from '../components/CodeView'
import ProviderMark from '../components/ProviderMark'
import { PROVIDERS } from '../providers'
import type { Provider, TerraformBundle, Tier } from '../types'
import { costText, tierTotal } from '../format'

interface Props {
  bundle: TerraformBundle
  tier: Tier
  target: { name: string; provider: Provider }
  confirmed: boolean
  locked: boolean
  onConfirm: (v: boolean) => void
}

export default function ReviewStep({ bundle, tier, target, confirmed, locked, onConfirm }: Props) {
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
          <strong className="c-add">{plan.add}</strong>
        </div>
        <div>
          <span>변경</span>
          <strong>{plan.change}</strong>
        </div>
        <div>
          <span>삭제</span>
          <strong className={plan.destroy ? 'c-del' : ''}>{plan.destroy}</strong>
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
          <span>월 예상</span>
          <strong>{costText(cost)}</strong>
        </div>
      </div>

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
        <input type="checkbox" checked={confirmed} disabled={locked} onChange={(e) => onConfirm(e.target.checked)} />
        <span>
          {isServer
            ? `${target.name}에 띄울 컨테이너 ${plan.add}개를 확인했습니다.`
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

import { useState } from 'react'
import Section from './Section'
import type { PlanSummary } from '../types'

interface Props {
  plan: PlanSummary | null
  enabled: boolean
  /** 승인 후 배포가 진행 중이거나 끝남 */
  approved: boolean
  onApprove: () => void
}

function lineClass(line: string) {
  const t = line.trimStart()
  if (t.startsWith('+')) return 'ln-add'
  if (t.startsWith('~')) return 'ln-change'
  if (t.startsWith('-')) return 'ln-del'
  if (t.startsWith('#')) return 'ln-comment'
  return undefined
}

export default function PlanSection({ plan, enabled, approved, onApprove }: Props) {
  const [checked, setChecked] = useState(false)
  const [open, setOpen] = useState(true)

  return (
    <Section
      no={4}
      title="배포 계획 확인과 승인"
      muted={!enabled}
      aside={
        plan && (
          <div className="plan-counts">
            <span className="c-add">추가 {plan.add}</span>
            <span className="c-change">변경 {plan.change}</span>
            <span className="c-del">삭제 {plan.destroy}</span>
          </div>
        )
      }
    >
      {!plan ? (
        <p className="placeholder">분석이 끝나면 terraform plan 결과를 먼저 보여 드립니다. 승인 전에는 아무것도 만들지 않습니다.</p>
      ) : (
        <>
          <button className="link-btn" onClick={() => setOpen(!open)}>
            {open ? 'plan 접기' : 'plan 펼치기'}
          </button>
          {open && (
            <pre className="plan">
              {plan.text.split('\n').map((l, i) => (
                <span key={i} className={lineClass(l)}>
                  {l + '\n'}
                </span>
              ))}
            </pre>
          )}
          <div className="approve-row">
            <label className="confirm">
              <input
                type="checkbox"
                checked={checked || approved}
                disabled={approved}
                onChange={(e) => setChecked(e.target.checked)}
              />
              생성될 리소스와 예상 비용을 확인했습니다
            </label>
            <button className="btn btn-primary" disabled={!checked || approved} onClick={onApprove}>
              {approved ? '승인됨' : '승인하고 배포'}
            </button>
          </div>
        </>
      )}
    </Section>
  )
}

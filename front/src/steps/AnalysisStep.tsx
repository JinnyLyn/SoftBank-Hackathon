import { useEffect, useState } from 'react'
import ProviderMark from '../components/ProviderMark'
import WorkProgress from '../components/WorkProgress'
import DockerfileDraft from '../components/DockerfileDraft'
import { DRAFT_BUILD } from '../api'
import { STAGE_SIZE } from '../progress'
import { PROVIDERS } from '../providers'
import { costText, TIER_META, tierTotal, usd } from '../format'
import type { Analysis, Choice, Recommendation, TierKey } from '../types'
import { useT } from '../i18n'

export type CodeState = 'idle' | 'loading' | 'ready' | 'error'

const CODE_TEXT: Record<CodeState, string> = {
  idle: '',
  loading: '배포 코드 만드는 중',
  ready: '배포 코드 준비됨',
  error: '배포 코드 생성 실패',
}

interface Props {
  analysis: Analysis
  /** 인프라 worker가 만든 구성·비용 계획. 아직 없으면 null (분석 결과는 먼저 보여 줌) */
  rec: Recommendation | null
  /** 계획을 기다리다 실패한 이유 */
  recError: string | null
  /** 계획을 기다리기 시작한 시각(ms). 진행 표시의 경과 시간 */
  recStartedAt: number | null
  onRetryRec: () => void
  choice: Choice | null
  /** 월 예산 한도. 넘는 칸은 고를 수 없음 */
  budget: number
  codeState: CodeState
  codeError?: string
  locked: boolean
  onChoice: (c: Choice) => void
  /** 계획이 없는 비교용 안을 누르면 그 안으로 다시 계획을 요청 */
  onRequestTier: (tier: TierKey) => void
  /** 다른 안의 실행 계획을 만드는 중이면 그 안 */
  replanning?: TierKey | null
}

const TIER_KEYS: TierKey[] = ['lean', 'balanced', 'roomy']

/** AWS 계획은 보통 1분 안팎에 옴. 이보다 오래 없으면 worker가 계획을 못 만든 경우(예산 부족 등)를 먼저 의심하도록 안내 */
const PLAN_SLOW_MS = 2 * 60 * 1000

function useSlow(startedAt: number | null, afterMs: number) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    setSlow(false)
    if (startedAt === null) return
    const left = startedAt + afterMs - Date.now()
    if (left <= 0) {
      setSlow(true)
      return
    }
    const timer = window.setTimeout(() => setSlow(true), left)
    return () => window.clearTimeout(timer)
  }, [startedAt, afterMs])
  return slow
}

export default function AnalysisStep({ analysis, rec, recError, recStartedAt, onRetryRec, ...plans }: Props) {
  const t = useT()
  const blockers = analysis.blockers ?? []
  const slowPlan = useSlow(rec || recError ? null : recStartedAt, PLAN_SLOW_MS)
  return (
    <div className="stack-lg">
      {blockers.length > 0 && (
        <div className="error" role="alert">
          <strong>{t('이 앱은 지금 배포할 수 없습니다.')}</strong>
          <ul className="blockers">
            {blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          <span>{t('코드를 고친 뒤 처음 화면에서 다시 올려 주세요.')}</span>
        </div>
      )}
      {analysis.dockerfileDraft && <DockerfileDraft draft={analysis.dockerfileDraft} />}
      <section>
        <h3 className="sub-title">{t('코드에서 찾은 것')}</h3>
        <dl className="kv">
          {analysis.stack.map((s, i) => (
            <div key={s.label} className="reveal" style={{ animationDelay: `${i * 90}ms` }}>
              <dt>{s.label}</dt>
              <dd>{s.value}</dd>
            </div>
          ))}
        </dl>
        <ul className="findings">
          {analysis.findings.map((f, i) => (
            <li
              key={f.title}
              className={'finding reveal is-' + f.level}
              style={{ animationDelay: `${(analysis.stack.length + i) * 90}ms` }}
            >
              <strong>{f.title}</strong>
              <span>{f.detail}</span>
            </li>
          ))}
        </ul>
        <details className="evidence">
          <summary>{t('판단 근거 {n}개', { n: analysis.evidence.length })}</summary>
          <ul>
            {analysis.evidence.map((e) => (
              <li key={e} className="mono">{e}</li>
            ))}
          </ul>
        </details>
      </section>

      {blockers.length > 0 || (analysis.dockerfileDraft && !DRAFT_BUILD) ? null : rec ? (
        <Plans rec={rec} {...plans} />
      ) : (
        <section>
          <h3 className="sub-title">{t('어디에, 어떤 크기로')}</h3>
          {recError ? (
            <div className="rec-wait is-error" role="alert">
              <span>{t('구성·비용 계획을 받지 못했습니다.')} {recError}</span>
              <button className="btn btn-ghost btn-sm" onClick={onRetryRec}>
                {t('다시 기다리기')}
              </button>
            </div>
          ) : (
            <WorkProgress
              title={t('구성과 비용 계획을 만들고 있습니다')}
              startedAt={recStartedAt ?? Date.now()}
              stageStartedAt={recStartedAt ?? Date.now()}
              stages={[
                { label: t('코드 올리기'), state: 'done', ...STAGE_SIZE.upload },
                { label: t('코드 분석'), state: 'done', ...STAGE_SIZE.analyze },
                { label: t('구성·비용 계획'), state: 'current', ...STAGE_SIZE.plan },
              ]}
              hints={PLAN_HINTS.map((h) => t(h))}
              note={t('보통 1~4분 걸립니다. 위의 분석 결과를 먼저 확인해 주세요. 승인하기 전에는 아무것도 만들지 않습니다.')}
            />
          )}
          {!recError && slowPlan && (
            <p className="hint" role="status">
              {t('계획이 늦어지고 있습니다. 가장 흔한 이유는 월 예산 {budget}이 가장 작은 AWS 구성보다 낮은 경우입니다(공용 로드밸런서·DB 몫이 포함돼 소규모 앱도 월 수십 달러부터). 이때는 계획이 만들어지지 않으니, 처음 화면에서 예산을 올려 다시 시작해 주세요.', { budget: usd(plans.budget) })}
            </p>
          )}
        </section>
      )}
    </div>
  )
}

// 계획 단계에서 인프라 worker가 실제로 하는 일
const PLAN_HINTS = [
  '사용 규모와 예산에 맞는 구성 크기를 고르고 있습니다',
  '공용 로드밸런서·DB 같은 기반 인프라 정보를 읽고 있습니다',
  '구성마다 한 달 비용을 계산하고 있습니다',
  'Terraform으로 무엇이 새로 생기는지 미리 계산하고 있습니다',
]

type PlansProps = Omit<Props, 'analysis' | 'rec' | 'recError' | 'recStartedAt' | 'onRetryRec'> & { rec: Recommendation }

function Plans({ rec, choice, budget, codeState, codeError, locked, onChoice, onRequestTier, replanning }: PlansProps) {
  const tr = useT()
  const option = rec.options.find((o) => o.connectionId === choice?.connectionId)
  const selected = option?.tiers.find((t) => t.key === choice?.tier)
  const isRec = (id: string, t: TierKey) => rec.recommended?.connectionId === id && rec.recommended?.tier === t

  const tierOf = (o: Recommendation['options'][number], k: TierKey) => o.tiers.find((t) => t.key === k)
  // 어느 대상에든 계획이 있는 크기만 열로 보여 줌 (계획이 1~2개뿐일 수 있음)
  const columns = TIER_KEYS.filter((k) => rec.options.some((o) => tierOf(o, k)))

  // 열(구성 크기)마다 가장 싼 대상. 계획이 있는 칸만 비교
  const cheapest = Object.fromEntries(
    columns.map((k) => {
      const costs = rec.options.flatMap((o) => {
        const t = tierOf(o, k)
        return t ? [{ id: o.connectionId, c: tierTotal(t) }] : []
      })
      return [k, costs.sort((a, b) => a.c - b.c)[0]?.id]
    }),
  ) as Partial<Record<TierKey, string>>

  return (
      <section>
        <h3 className="sub-title">{tr('어디에, 어떤 크기로')}</h3>
        <p className={'rec-reason' + (rec.recommended ? '' : ' is-blocked')}>{rec.reason}</p>

        {rec.options.length === 0 ? (
          <p className="muted">{tr('비교할 배포 대상이 없습니다. 첫 화면이나 연결 관리 탭에서 하나 이상 연결해 주세요.')}</p>
        ) : (
          <div className="matrix-wrap">
            <table className="matrix">
              <thead>
                <tr>
                  <th>{tr('배포 대상')}</th>
                  {columns.map((k) => (
                    <th key={k}>
                      {tr(TIER_META[k].label)}
                      <small>{tr(TIER_META[k].fit)}</small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rec.options.map((o) => (
                  <tr key={o.connectionId}>
                    <th scope="row">
                      <ProviderMark provider={o.provider} />
                      <span>
                        {tr(o.name)}
                        <small>{tr(PROVIDERS[o.provider].label)}</small>
                      </span>
                    </th>
                    {columns.map((k) => {
                      const t = tierOf(o, k)
                      if (!t)
                        return (
                          <td key={k}>
                            <button className="cell is-empty" disabled>
                              <span className="cell-tags" />
                              <strong>-</strong>
                              <small>{tr('이 크기의 계획 없음')}</small>
                            </button>
                          </td>
                        )
                      const on = choice?.connectionId === o.connectionId && choice?.tier === t.key
                      const over = tierTotal(t) > budget
                      // 계획이 없는 비교용 안: 누르면 이 안으로 다시 계획(약 1분, 다시 승인)
                      const compareOnly = t.planned === false
                      const recommended = t.suggested ?? isRec(o.connectionId, t.key)
                      return (
                        <td key={t.key}>
                          <button
                            className={'cell' + (on || replanning === t.key ? ' is-on' : '') + (over ? ' is-over' : '')}
                            disabled={locked || over}
                            title={
                              over
                                ? tr('월 예산 ${budget}을 넘습니다', { budget })
                                : compareOnly
                                  ? tr('누르면 이 안으로 배포 계획을 다시 만듭니다(약 1분).')
                                  : undefined
                            }
                            aria-pressed={on}
                            onClick={() =>
                              compareOnly ? onRequestTier(t.key) : onChoice({ connectionId: o.connectionId, tier: t.key })
                            }
                          >
                            <span className="cell-tags">
                              {recommended && <span className="badge">{tr('추천')}</span>}
                              {cheapest[t.key] === o.connectionId && rec.options.length > 1 && !over && (
                                <span className="tag-low">{tr('최저')}</span>
                              )}
                              {over && <span className="tag-over">{tr('예산 초과')}</span>}
                              {t.rankLabel && <span className="tag-rank">{tr(t.rankLabel)}</span>}
                            </span>
                            <strong>{costText(tierTotal(t))}</strong>
                            {t.usualUsd !== undefined && t.usualUsd !== tierTotal(t) && (
                              <small className="cell-usual">{tr('평소 {usd} · 부하 최대 기준', { usd: usd(t.usualUsd) })}</small>
                            )}
                            <small>{t.headline}</small>
                            {replanning === t.key ? (
                              <small className="cell-note is-working">
                                <span className="pulse" aria-hidden /> {tr('이 안의 실행 계획을 만드는 중… (1분 안팎, 분석은 다시 하지 않음)')}
                              </small>
                            ) : (
                              compareOnly && !over && <small className="cell-note">{tr('누르면 이 안의 실행 계획을 만듭니다(1분 안팎)')}</small>
                            )}
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rec.options.some((o) => o.tiers.some((t) => t.planned === false)) && (
          <p className="hint">
            {tr('실행 계획은 고른 안 하나로 만듭니다. 다른 안을 누르면 분석·비용 계산은 그대로 두고 그 안의 실행 계획만 새로 만든 뒤, 바뀐 계획으로 다시 승인합니다.')}
          </p>
        )}

        {option && selected && (
          <div className="tier-detail">
            <h4>
              <ProviderMark provider={option.provider} /> {tr(option.name)} · {tr(selected.label)} — {selected.headline}
              {codeState !== 'idle' && (
                <span className={'code-state is-' + codeState} title={codeError}>
                  {tr(CODE_TEXT[codeState])}
                </span>
              )}
            </h4>
            <table className="table">
              <thead>
                <tr>
                  <th>{tr('리소스')}</th>
                  <th>{tr('사양')}</th>
                  <th>{tr('이렇게 고른 이유')}</th>
                  <th className="num">{tr('월')}</th>
                </tr>
              </thead>
              <tbody>
                {selected.resources.map((r) => (
                  <tr key={r.service}>
                    <td className="nowrap">
                      <strong>{r.service}</strong>
                    </td>
                    <td className="mono small">{r.spec}</td>
                    <td className="muted">{r.why}</td>
                    <td className="num">{r.monthlyUsd > 0 ? usd(r.monthlyUsd) : '-'}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>{tr('합계')}{selected.usageNote && <span className="muted"> · {tr('서버 자원 {note}', { note: selected.usageNote })}</span>}</td>
                  <td className="num">{costText(tierTotal(selected))}</td>
                </tr>
              </tfoot>
            </table>
            <p className="tradeoff">
              <span>{tr('감수할 점')}</span> {selected.tradeoff}
            </p>
            <ul className="assumptions">
              {rec.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
  )
}

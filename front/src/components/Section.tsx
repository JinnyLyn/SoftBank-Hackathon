import type { ReactNode } from 'react'

interface Props {
  no: number
  title: string
  /** 앞 단계가 안 끝났으면 흐리게 */
  muted?: boolean
  aside?: ReactNode
  children?: ReactNode
}

export default function Section({ no, title, muted, aside, children }: Props) {
  return (
    <section className={'card' + (muted ? ' is-muted' : '')}>
      <div className="card-head">
        <h2>
          <span className="step-no">{no}</span>
          {title}
        </h2>
        {aside}
      </div>
      {children && <div className="card-body">{children}</div>}
    </section>
  )
}

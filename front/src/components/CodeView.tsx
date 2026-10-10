import { useEffect, useRef, type ReactNode } from 'react'

type Kind = 'plan' | 'hcl' | 'log'

function hclLine(line: string): ReactNode {
  if (line.trimStart().startsWith('#')) return <span className="tk-comment">{line}</span>
  // 문자열만 색칠. 나머지는 그대로
  return line.split(/("(?:[^"\\]|\\.)*")/).map((part, i) =>
    part.startsWith('"') ? (
      <span key={i} className="tk-string">{part}</span>
    ) : (
      <span key={i} className={i === 0 && /^(resource|variable|output|provider|terraform|data|locals)\b/.test(part) ? 'tk-kw' : undefined}>
        {part}
      </span>
    ),
  )
}

function lineClass(kind: Kind, line: string) {
  const t = line.trimStart()
  if (kind === 'plan') {
    if (t.startsWith('+')) return 'tk-add'
    if (t.startsWith('~')) return 'tk-change'
    if (t.startsWith('-')) return 'tk-del'
    if (t.startsWith('#')) return 'tk-comment'
    if (t.startsWith('Plan:')) return 'tk-strong'
  }
  if (kind === 'log') {
    if (t.startsWith('$')) return 'tk-cmd'
    if (/fail|refused|error/i.test(t)) return 'tk-del'
    if (/complete|passed|success/i.test(t)) return 'tk-add'
  }
  return undefined
}

interface Props {
  kind: Kind
  text: string | string[]
  /** 새 줄이 추가되면 아래로 스크롤 */
  follow?: boolean
  numbered?: boolean
}

export default function CodeView({ kind, text, follow, numbered }: Props) {
  const lines = Array.isArray(text) ? text : text.split('\n')
  const ref = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [follow, lines.length])

  return (
    <pre ref={ref} className={'code' + (numbered ? ' is-numbered' : '')}>
      {lines.map((l, i) => (
        <span key={i} className={'code-line ' + (lineClass(kind, l) ?? '')}>
          {numbered && <span className="ln">{i + 1}</span>}
          {kind === 'hcl' ? hclLine(l) : l}
          {'\n'}
        </span>
      ))}
    </pre>
  )
}

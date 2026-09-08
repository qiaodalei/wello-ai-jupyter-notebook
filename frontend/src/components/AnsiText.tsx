const ANSI_RE = /\x1b\[([0-9;]*)m/g

const CODE_CLASS: Record<string, string> = {
  '0': '',
  '1': 'ansi-bold',
  '3': 'ansi-italic',
  '4': 'ansi-underline',
  '30': 'ansi-black',
  '31': 'ansi-red',
  '32': 'ansi-green',
  '33': 'ansi-yellow',
  '34': 'ansi-blue',
  '35': 'ansi-magenta',
  '36': 'ansi-cyan',
  '37': 'ansi-white',
  '90': 'ansi-gray',
  '91': 'ansi-red',
  '92': 'ansi-green',
  '93': 'ansi-yellow',
  '94': 'ansi-blue',
  '95': 'ansi-magenta',
  '96': 'ansi-cyan',
}

function classesFrom(codes: string) {
  if (!codes) return []
  const next: string[] = []
  for (const part of codes.split(';')) {
    if (part === '0' || part === '') {
      next.length = 0
      continue
    }
    const cls = CODE_CLASS[part]
    if (cls) next.push(cls)
  }
  return next
}

export function AnsiText({ text }: { text: string }) {
  const parts: { text: string; className: string }[] = []
  let last = 0
  let current: string[] = []
  const src = text.replace(/\r\n/g, '\n')
  src.replace(ANSI_RE, (match, codes: string, offset: number) => {
    if (offset > last) {
      parts.push({ text: src.slice(last, offset), className: current.join(' ') })
    }
    current = classesFrom(codes)
    last = offset + match.length
    return match
  })
  if (last < src.length) {
    parts.push({ text: src.slice(last), className: current.join(' ') })
  }
  if (!parts.length) return <>{src}</>
  return (
    <>
      {parts.map((p, i) =>
        p.className ? (
          <span key={i} className={p.className}>
            {p.text}
          </span>
        ) : (
          <span key={i}>{p.text}</span>
        )
      )}
    </>
  )
}

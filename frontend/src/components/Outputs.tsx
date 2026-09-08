import type { CellOutput } from '../types'
import { AnsiText } from './AnsiText'

function textFrom(data: Record<string, string>, key: string) {
  return data[key] || ''
}

function PlainTraceback({ text }: { text: string }) {
  return (
    <>
      {text.split('\n').map((line, i) => {
        const file = line.match(/^(File )(.+?)(, line )(\d+)(.*)$/)
        if (file) {
          return (
            <span key={i}>
              <span className="ansi-green">{file[1]}</span>
              <span className="ansi-cyan">{file[2]}</span>
              <span className="ansi-green">{file[3]}</span>
              <span className="ansi-green">{file[4]}</span>
              {file[5]}
              {'\n'}
            </span>
          )
        }
        const err = line.match(/^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning))(\s+.*)?$/)
        if (err) {
          return (
            <span key={i}>
              <span className="ansi-red ansi-bold">{err[1]}</span>
              {err[2] || ''}
              {'\n'}
            </span>
          )
        }
        if (line.includes('---->') || line.startsWith('-----')) {
          return (
            <span key={i} className={line.startsWith('-----') ? 'ansi-red' : 'ansi-green'}>
              {line}
              {'\n'}
            </span>
          )
        }
        return (
          <span key={i}>
            {line}
            {'\n'}
          </span>
        )
      })}
    </>
  )
}

export function Outputs({ outputs }: { outputs: CellOutput[] }) {
  if (!outputs?.length) return null
  return (
    <div className="outputs">
      {outputs.map((out, i) => {
        if (out.output_type === 'stream') {
          return (
            <pre key={i} className={`out-stream ${out.name === 'stderr' ? 'stderr' : ''}`}>
              <AnsiText text={out.text} />
            </pre>
          )
        }
        if (out.output_type === 'error') {
          const body = out.traceback?.length ? out.traceback.join('\n') : `${out.ename}: ${out.evalue}`
          const colored = body.includes('\u001b[')
          return (
            <pre key={i} className="out-error">
              {colored ? <AnsiText text={body} /> : <PlainTraceback text={body} />}
            </pre>
          )
        }
        const data = out.data || {}
        if (data['image/png']) {
          return <img key={i} className="out-img" alt="plot" src={`data:image/png;base64,${data['image/png']}`} />
        }
        if (data['image/jpeg']) {
          return <img key={i} className="out-img" alt="plot" src={`data:image/jpeg;base64,${data['image/jpeg']}`} />
        }
        if (data['text/html']) {
          return (
            <div
              key={i}
              className="out-html"
              dangerouslySetInnerHTML={{ __html: textFrom(data, 'text/html') }}
            />
          )
        }
        if (data['text/plain']) {
          return (
            <pre key={i} className="out-plain">
              <AnsiText text={data['text/plain']} />
            </pre>
          )
        }
        return null
      })}
    </div>
  )
}

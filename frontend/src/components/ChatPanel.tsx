import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { api } from '../api'
import { chatOf, useApp } from '../store'

const PHASE: Record<string, string> = {
  planning: 'planning',
  thinking: 'thinking',
  writing: 'writing cells',
  executing: 'running',
  reading: 'reading output',
  repair: 'fixing',
  waiting: 'waiting',
  stopping: 'stopping',
  running: 'running',
}

const EXAMPLES = [
  '用 MNIST 训练一个数字识别模型，PyTorch，1 个 epoch，中文讲解',
  '教我如何使用 pandas 这个库',
  '用 sympy 解 x³ - 2x + 1 = 0，并画出这个函数的曲线',
]

export function ChatPanel() {
  // The panel always shows the open notebook's own conversation; the others
  // keep updating in the background.
  const sessionName = useApp((s) => s.notebook?.name ?? null)
  const chat = useApp((s) => chatOf(s, s.notebook?.name))
  const { messages, live, agent } = chat
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const running = !!PHASE[agent.phase]

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, agent.phase, live.content, live.reasoning])

  const send = async (value?: string) => {
    const message = (value ?? text).trim()
    if (!message) return
    setText('')
    setBusy(true)
    try {
      await api.chat(message)
    } catch (err) {
      useApp.getState().pushMessage({
        id: Math.random().toString(36).slice(2),
        role: 'assistant',
        content: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="chat">
      <div className="chat-head">
        <span>CHAT</span>
        <span className="right">
          {running && <span className="phase">{PHASE[agent.phase]}</span>}
          <button
            className="tb icon"
            title={`清空 ${sessionName || '当前'} 的对话上下文`}
            disabled={running || (messages.length === 0 && !live.content)}
            onClick={() => {
              if (!window.confirm('清空这个 notebook 的对话上下文？单元格不会动。')) return
              void useApp.getState().clearContext()
            }}
          >
            ⌫
          </button>
          <button className="tb icon" title="Hide chat" onClick={() => useApp.getState().setChatOpen(false)}>
            ✕
          </button>
        </span>
      </div>

      <div className="messages">
        {messages.length === 0 && (
          <>
            <div className="chat-empty">
              说一个能用 Notebook 做的事——分析数据、画图、算个数、写段脚本、验证一个算法、学一个库的用法都行。
              文档和代码会一步步写进左边的单元格，每步执行完看到结果才写下一步。
              <br />
              对话上下文跟着 {sessionName || '当前 notebook'} 走，只带最近 5 轮；
              每本 notebook 各有自己的会话和内核，可以同时跑、随时切换查看。
            </div>
            {EXAMPLES.map((ex) => (
              <button key={ex} className="suggest" onClick={() => void send(ex)}>
                {ex}
              </button>
            ))}
          </>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <span className="who">{m.role === 'user' ? '你' : m.role === 'system' ? '系统' : 'Wello'}</span>
            {m.reasoning && (
              <details className="fold">
                <summary>思考过程 · {m.reasoning.length} 字</summary>
                <div className="reasoning">{m.reasoning}</div>
              </details>
            )}
            {m.role === 'assistant' ? (
              m.content && (
                <div className="md">
                  <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                    {m.content}
                  </Markdown>
                </div>
              )
            ) : (
              m.content
            )}
          </div>
        ))}
        {live.content && (
          <div className="msg assistant">
            <span className="who">Wello</span>
            <div className="md">
              <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                {live.content}
              </Markdown>
            </div>
            <span className="caret" />
          </div>
        )}
        {!live.content && (live.reasoning || running) && (
          <div className="msg trace">
            <span className="who">思考中<span className="ellipsis" /></span>
            <span className="reasoning">{live.reasoning || PHASE[agent.phase]}</span>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <textarea
          value={text}
          placeholder="描述你想做的事"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void send()
            }
          }}
        />
        <div className="composer-row">
          <span className="hint">Enter 发送 · ⇧Enter 换行</span>
          {running ? (
            <button className="btn" onClick={() => void api.stopAgent(sessionName)}>
              停止
            </button>
          ) : (
            <button className="btn primary" disabled={busy || !text.trim()} onClick={() => void send()}>
              发送
            </button>
          )}
        </div>
      </div>
    </aside>
  )
}

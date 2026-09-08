import { useEffect, useRef, useState } from 'react'
import { api, exportUrl } from './api'
import { CellView } from './components/CellView'
import { ChatPanel } from './components/ChatPanel'
import { ConfigPage } from './components/ConfigPage'
import {
  deleteCell,
  insertAbove,
  insertBelow,
  selectOffset,
  setCellType,
} from './commands'
import { runAll, runCell, useApp } from './store'
import type { Chat } from './store'
import { connectWs, disconnectWs } from './ws'

const KERNEL_LABEL: Record<string, string> = {
  idle: 'Idle',
  busy: 'Busy',
  starting: 'Starting',
  dead: 'Disconnected',
}

/** A notebook is busy while its own agent run is in flight. */
function isBusy(chats: Record<string, Chat>, name: string) {
  const phase = chats[name]?.agent.phase
  return !!phase && phase !== 'idle'
}

/** A file going up out of a tray — import, not download. */
function UploadIcon() {
  return (
    <svg className="ico" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 10V2.6M8 2.6 5.4 5.2M8 2.6l2.6 2.6" />
      <path d="M2.8 9.4v3.2h10.4V9.4" />
    </svg>
  )
}

function isTyping(target: EventTarget | null) {
  const el = target as HTMLElement | null
  if (!el) return false
  if (el.isContentEditable) return true
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return !!el.closest?.('.cm-editor')
}

function NotebookApp() {
  const notebook = useApp((s) => s.notebook)
  const files = useApp((s) => s.files)
  const kernel = useApp((s) => s.kernel)
  const leftOpen = useApp((s) => s.leftOpen)
  const chatOpen = useApp((s) => s.chatOpen)
  const selectedId = useApp((s) => s.selectedId)
  const chats = useApp((s) => s.chats)
  const connected = useApp((s) => s.connected)
  const error = useApp((s) => s.error)
  const [name, setName] = useState('')
  const lastD = useRef(0)
  const importRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    void useApp.getState().loadAll()
    connectWs()
    const timer = window.setInterval(() => {
      if (useApp.getState().notebook?.dirty) void api.saveNotebook().catch(() => undefined)
    }, 30000)
    return () => {
      window.clearInterval(timer)
      disconnectWs()
    }
  }, [])

  useEffect(() => {
    if (notebook?.name) setName(notebook.name.replace(/\.ipynb$/, ''))
  }, [notebook?.name])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target)) return
      const id = useApp.getState().selectedId
      if (e.key === 'Enter' && e.shiftKey) {
        e.preventDefault()
        if (id) void runCell(id).then(() => selectOffset(1))
        return
      }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        if (id) void runCell(id)
        return
      }
      if (e.key === 'ArrowDown' || e.key === 'j') return selectOffset(1)
      if (e.key === 'ArrowUp' || e.key === 'k') return selectOffset(-1)
      if (e.key === 'a') return void insertAbove('code')
      if (e.key === 'b') return void insertBelow('code')
      if (e.key === 'm') return void setCellType('markdown')
      if (e.key === 'y') return void setCellType('code')
      if (e.key === 'd') {
        const now = Date.now()
        if (now - lastD.current < 600) {
          lastD.current = 0
          void deleteCell()
        } else {
          lastD.current = now
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const refreshFiles = async () => {
    const data = await api.files()
    useApp.setState({ files: data.files })
  }

  const openFile = async (name: string) => {
    // Switching is free: the run keeps going in the notebook it started in.
    try {
      useApp.getState().setNotebook(await api.openNotebook(name))
      useApp.setState({ error: null })
    } catch (err) {
      useApp.setState({ error: err instanceof Error ? err.message : String(err) })
    }
  }

  const newFile = async () => {
    try {
      useApp.getState().setNotebook(await api.newNotebook())
      await refreshFiles()
    } catch (err) {
      useApp.setState({ error: err instanceof Error ? err.message : String(err) })
    }
  }

  const removeFile = async (name: string) => {
    if (!window.confirm(`删除 ${name}？文件会从 notebooks 目录里移除，无法撤销。`)) return
    try {
      const res = await api.deleteFile(name)
      useApp.setState({ files: res.files, error: null })
      if (res.switched) useApp.getState().setNotebook(res.notebook)
    } catch (err) {
      useApp.setState({ error: err instanceof Error ? err.message : String(err) })
    }
  }

  const importNotebooks = async (picked: File[]) => {
    try {
      const res = await api.importFiles(picked)
      useApp.setState({ files: res.files, error: null })
      useApp.getState().setNotebook(res.notebook)
    } catch (err) {
      useApp.setState({ error: err instanceof Error ? err.message : String(err) })
    }
  }

  const rename = () => {
    const next = (name.trim() || 'Untitled') + '.ipynb'
    if (!notebook || next === notebook.name) return
    void api.saveNotebook(next).then((nb) => {
      useApp.getState().setNotebook(nb)
      void refreshFiles()
    })
  }

  const codeCells = notebook?.cells.filter((c) => c.cell_type === 'code').length ?? 0

  return (
    <div className="shell">
      <div className="tabbar">
        <div className="tab">
          <span>{notebook?.dirty ? <span className="dot-dirty" /> : null}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} onBlur={rename} />
          <span className="ext">.ipynb</span>
        </div>
        <div className="fill" />
      </div>

      <div className="toolbar">
        <button className="tb icon" title="Toggle explorer" onClick={() => useApp.getState().setLeftOpen(!leftOpen)}>
          ☰
        </button>
        <span className="vr" />
        <button className="tb" onClick={() => void insertBelow('code')}>
          + Code
        </button>
        <button className="tb" onClick={() => void insertBelow('markdown')}>
          + Markdown
        </button>
        <span className="vr" />
        <button className="tb" onClick={() => void runAll()}>
          Run All
        </button>
        <button
          className="tb"
          onClick={() => void api.clearAllOutputs().then((nb) => useApp.getState().setNotebook(nb))}
        >
          Clear Outputs
        </button>
        <button className="tb" onClick={() => void api.interrupt()}>
          Interrupt
        </button>
        <button className="tb" onClick={() => void api.restart()}>
          Restart
        </button>
        <span className="vr" />
        <button
          className="tb"
          onClick={() => void api.saveNotebook().then((nb) => useApp.getState().setNotebook(nb))}
        >
          Save
        </button>
        <a className="tb" href={exportUrl()}>
          Export
        </a>
        <div className="fill" />
        <span className="kernel" title="Local Python kernel">
          <span className={`dot ${kernel}`} />
          Python 3 · {KERNEL_LABEL[kernel] || kernel}
        </span>
        <button
          className={`tb ${chatOpen ? 'on' : ''}`}
          title="Toggle chat"
          onClick={() => useApp.getState().setChatOpen(!chatOpen)}
        >
          Chat
        </button>
      </div>

      <div className="body">
        {leftOpen && (
          <aside className="side">
            <div className="side-head">
              NOTEBOOKS
              <span className="side-actions">
                <button
                  className="tb icon"
                  title="Import .ipynb"
                  onClick={() => importRef.current?.click()}
                >
                  <UploadIcon />
                </button>
                <button className="tb icon" title="New notebook" onClick={() => void newFile()}>
                  +
                </button>
              </span>
            </div>
            <input
              ref={importRef}
              type="file"
              accept=".ipynb,application/x-ipynb+json"
              multiple
              hidden
              onChange={(e) => {
                // Copy the list out before resetting the input: the handler
                // awaits, and clearing `value` would empty the FileList.
                const picked = Array.from(e.target.files ?? [])
                e.target.value = ''
                if (picked.length) void importNotebooks(picked)
              }}
            />
            {files.map((f) => (
              <div key={f.name} className={`file ${notebook?.name === f.name ? 'active' : ''}`}>
                <button className="file-name" title={f.name} onClick={() => void openFile(f.name)}>
                  {f.name}
                </button>
                {isBusy(chats, f.name) && <span className="file-busy" title="Agent 正在这本里跑" />}
                <button className="file-rm" title="Delete notebook" onClick={() => void removeFile(f.name)}>
                  ✕
                </button>
              </div>
            ))}
            {files.length === 0 && <div className="side-empty">还没有 notebook</div>}
          </aside>
        )}

        <main className="notebook" tabIndex={-1}>
          {error && <div className="banner">{error}</div>}
          {notebook?.cells.length === 0 && (
            <div className="empty">Notebook is empty. Use “+ Code” above, or ask in the chat panel.</div>
          )}
          {notebook?.cells.map((cell, i) => (
            <CellView key={cell.id} cell={cell} index={i} />
          ))}
          <div className="cell-gap">
            <div className="rule">
              <button onClick={() => void insertBelow('code')}>+ Code</button>
              <button onClick={() => void insertBelow('markdown')}>+ Markdown</button>
            </div>
          </div>
        </main>

        {chatOpen && <ChatPanel />}
      </div>

      <div className="statusbar">
        <button className="st" title="Toggle explorer" onClick={() => useApp.getState().setLeftOpen(!leftOpen)}>
          ☰ Explorer
        </button>
        <button className="st" title="Toggle chat" onClick={() => useApp.getState().setChatOpen(!chatOpen)}>
          ⌘ Chat {chatOpen ? 'on' : 'off'}
        </button>
        <div className="fill" />
        <span className="st plain">
          {codeCells} code · {notebook?.cells.length ?? 0} cells
        </span>
        <span className="st plain">{selectedId ? 'Cell selected' : 'No selection'}</span>
        <span className="st plain">
          <span className={`dot ${kernel}`} />
          {connected ? `Python 3 · ${KERNEL_LABEL[kernel] || kernel}` : 'Reconnecting…'}
        </span>
        <a className="st" href="#config" title="Model configuration">
          ⚙ Model
        </a>
      </div>
    </div>
  )
}

export default function App() {
  const [hash, setHash] = useState(window.location.hash)

  useEffect(() => {
    const onHash = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (hash === '#config') return <ConfigPage />
  return <NotebookApp />
}

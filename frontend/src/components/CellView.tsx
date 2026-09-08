import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { Cell } from '../types'
import { deleteCell, insertAt, moveCell, setCellType } from '../commands'
import { runCell, useApp } from '../store'
import { CodeEditor } from './CodeEditor'
import { Outputs } from './Outputs'

export function CellView({ cell, index }: { cell: Cell; index: number }) {
  const selected = useApp((s) => s.selectedId === cell.id)
  const editingId = useApp((s) => s.editingId)
  const select = useApp((s) => s.select)
  const setEditing = useApp((s) => s.setEditing)
  const updateLocalSource = useApp((s) => s.updateLocalSource)
  const [mdEdit, setMdEdit] = useState(false)
  const areaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (mdEdit) areaRef.current?.focus()
  }, [mdEdit])

  useEffect(() => {
    if (!selected && mdEdit) setMdEdit(false)
  }, [selected, mdEdit])

  const runNext = async () => {
    await runCell(cell.id)
    const cells = useApp.getState().notebook?.cells || []
    const idx = cells.findIndex((c) => c.id === cell.id)
    if (idx < 0) return
    // Jupyter behaviour: running the last cell appends a fresh one below.
    if (idx === cells.length - 1) await insertAt(cells.length, 'code')
    else select(cells[idx + 1].id)
  }
  const runStay = () => runCell(cell.id)
  const runInsert = async () => {
    await runCell(cell.id)
    await insertAt(index + 1, 'code')
  }

  const prompt =
    cell.status === 'running' ? '[*]' : cell.execution_count != null ? `[${cell.execution_count}]` : '[ ]'

  const isMd = cell.cell_type === 'markdown'
  const showMdEditor = isMd && (mdEdit || editingId === cell.id || (selected && !cell.source.trim()))

  return (
    <div>
      <div className="cell-gap">
        <div className="rule">
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => void insertAt(index, 'code')}>
            + Code
          </button>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => void insertAt(index, 'markdown')}>
            + Markdown
          </button>
        </div>
      </div>

      <div
        className={`cell ${selected ? 'selected' : ''} ${cell.status === 'error' ? 'err' : ''}`}
        onMouseDown={() => select(cell.id)}
      >
        <div className="gutter">
          {!isMd && (
            <>
              <button
                className={`run ${cell.status === 'running' ? 'busy' : ''}`}
                title="Run cell (⇧⏎)"
                onClick={(e) => {
                  e.stopPropagation()
                  void runStay()
                }}
              >
                {cell.status === 'running' ? (
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
                    <rect x="1" y="1" width="10" height="10" rx="1" />
                  </svg>
                ) : (
                  <svg width="12" height="13" viewBox="0 0 12 13" fill="currentColor">
                    <path d="M1 0.5v12l10.5-6L1 0.5z" />
                  </svg>
                )}
              </button>
              <div className={`prompt ${cell.status === 'error' ? 'error' : ''}`}>{prompt}</div>
            </>
          )}
        </div>

        <div>
          <div className="input">
            {isMd ? (
              showMdEditor ? (
                <textarea
                  ref={areaRef}
                  className="md-edit"
                  value={cell.source}
                  placeholder="Markdown"
                  onFocus={() => setEditing(cell.id)}
                  onBlur={() => {
                    setMdEdit(false)
                    setEditing(null)
                  }}
                  onChange={(e) => updateLocalSource(cell.id, e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && e.shiftKey) {
                      e.preventDefault()
                      setMdEdit(false)
                      setEditing(null)
                      e.currentTarget.blur()
                    }
                    if (e.key === 'Escape') {
                      setMdEdit(false)
                      setEditing(null)
                      e.currentTarget.blur()
                    }
                  }}
                />
              ) : (
                <div className="md-view" onDoubleClick={() => setMdEdit(true)} title="Double-click to edit">
                  {cell.source.trim() ? (
                    <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                      {cell.source}
                    </Markdown>
                  ) : (
                    <span className="md-placeholder">Empty markdown cell — double-click to edit</span>
                  )}
                </div>
              )
            ) : (
              <CodeEditor
                value={cell.source}
                onFocus={() => setEditing(cell.id)}
                onBlur={() => setEditing(null)}
                onChange={(v) => updateLocalSource(cell.id, v)}
                onRun={() => void runNext()}
                onRunStay={() => void runStay()}
                onRunInsert={() => void runInsert()}
              />
            )}
            <select
              className="lang"
              value={cell.cell_type}
              title="Cell language"
              onMouseDown={(e) => e.stopPropagation()}
              onChange={(e) => void setCellType(e.target.value as 'code' | 'markdown', cell.id)}
            >
              <option value="code">Python</option>
              <option value="markdown">Markdown</option>
            </select>
          </div>
          {!isMd && <Outputs outputs={cell.outputs || []} />}
        </div>

        <div className="cell-actions">
          <button title="Move up" onClick={() => void moveCell(-1, cell.id)}>
            ↑
          </button>
          <button title="Move down" onClick={() => void moveCell(1, cell.id)}>
            ↓
          </button>
          <button className="rm" title="Delete cell" onClick={() => void deleteCell(cell.id)}>
            ✕
          </button>
        </div>
      </div>
    </div>
  )
}

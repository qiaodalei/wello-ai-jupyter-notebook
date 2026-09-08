import { api } from './api'
import { useApp } from './store'

async function refresh() {
  await useApp.getState().loadAll()
}

function selected() {
  const { selectedId, notebook } = useApp.getState()
  if (!notebook) return null
  return notebook.cells.find((c) => c.id === selectedId) || null
}

function indexOfSelected() {
  const { selectedId, notebook } = useApp.getState()
  if (!notebook) return -1
  return notebook.cells.findIndex((c) => c.id === selectedId)
}

export async function insertAbove(cell_type: 'code' | 'markdown' = 'code') {
  const idx = indexOfSelected()
  const cell = await api.insertCell({ index: idx < 0 ? 0 : idx, cell_type })
  await refresh()
  useApp.getState().select(cell.id)
}

export async function insertBelow(cell_type: 'code' | 'markdown' = 'code') {
  const cur = selected()
  const cell = cur
    ? await api.insertCell({ after_cell_id: cur.id, cell_type })
    : await api.insertCell({ cell_type })
  await refresh()
  useApp.getState().select(cell.id)
}

export async function insertAt(index: number, cell_type: 'code' | 'markdown' = 'code') {
  const cell = await api.insertCell({ index, cell_type })
  await refresh()
  useApp.getState().select(cell.id)
}

export async function deleteCell(id?: string) {
  const target = id || useApp.getState().selectedId
  if (!target) return
  const { notebook } = useApp.getState()
  const cells = notebook?.cells || []
  const idx = cells.findIndex((c) => c.id === target)
  const nextId = cells[idx + 1]?.id || cells[idx - 1]?.id || null
  await api.deleteCell(target)
  await refresh()
  const after = useApp.getState().notebook?.cells || []
  useApp.getState().select(after.some((c) => c.id === nextId) ? nextId : after[0]?.id ?? null)
}

export async function moveCell(delta: number, id?: string) {
  const target = id || useApp.getState().selectedId
  if (!target) return
  await api.moveCell(target, delta).catch(() => undefined)
  await refresh()
  useApp.getState().select(target)
}

export async function setCellType(cell_type: 'code' | 'markdown', id?: string) {
  const target = id || useApp.getState().selectedId
  if (!target) return
  await useApp.getState().flushSource(target)
  await api.patchCell(target, { cell_type })
  await refresh()
  useApp.getState().select(target)
}

export async function clearOutputs(id?: string) {
  const target = id || useApp.getState().selectedId
  if (!target) return
  await api.clearOutputs(target)
  await refresh()
}

export function selectOffset(delta: number) {
  const { notebook, selectedId } = useApp.getState()
  const cells = notebook?.cells || []
  if (!cells.length) return
  const idx = cells.findIndex((c) => c.id === selectedId)
  const next = Math.max(0, Math.min(cells.length - 1, (idx < 0 ? 0 : idx) + delta))
  useApp.getState().select(cells[next].id)
}

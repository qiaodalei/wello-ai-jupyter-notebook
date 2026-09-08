import { create } from 'zustand'
import type { AgentStatus, Cell, ChatMessage, FileInfo, KernelStatus, Notebook, Settings } from './types'
import { api } from './api'

let patchTimer: number | null = null
const pendingSources = new Map<string, string>()

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

function readFlag(key: string, fallback: boolean) {
  try {
    const raw = localStorage.getItem(key)
    return raw == null ? fallback : raw === '1'
  } catch {
    return fallback
  }
}

function writeFlag(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* ignore */
  }
}

interface AppState {
  notebook: Notebook | null
  files: FileInfo[]
  selectedId: string | null
  editingId: string | null
  kernel: KernelStatus
  /** One conversation per notebook, all updated live even while off screen. */
  chats: Record<string, Chat>
  settings: Settings | null
  leftOpen: boolean
  chatOpen: boolean
  connected: boolean
  error: string | null
  clipboard: Cell | null
  loadAll: () => Promise<void>
  setNotebook: (nb: Notebook) => void
  select: (id: string | null) => void
  setEditing: (id: string | null) => void
  updateLocalSource: (id: string, source: string) => void
  flushSource: (id?: string) => Promise<void>
  applyWs: (event: Record<string, unknown>) => void
  setLeftOpen: (v: boolean) => void
  setChatOpen: (v: boolean) => void
  setSettings: (s: Settings) => void
  pushMessage: (msg: ChatMessage) => void
  setClipboard: (cell: Cell | null) => void
  syncSession: (name: string) => Promise<void>
  syncKernel: (name: string) => Promise<void>
  clearContext: () => Promise<void>
}

export interface Chat {
  messages: ChatMessage[]
  /** Text streaming in from the current model round, not yet a message. */
  live: { reasoning: string; content: string }
  agent: AgentStatus
  /** False until this notebook's history has been read back from the server. */
  loaded: boolean
}

const emptyAgent: AgentStatus = { phase: 'idle', detail: '' }

export const EMPTY_CHAT: Chat = {
  messages: [],
  live: { reasoning: '', content: '' },
  agent: emptyAgent,
  loaded: false,
}

export const useApp = create<AppState>((set, get) => ({
  notebook: null,
  files: [],
  selectedId: null,
  editingId: null,
  kernel: 'starting',
  chats: {},
  settings: null,
  leftOpen: readFlag('wello.leftOpen', true),
  chatOpen: readFlag('wello.chatOpen', true),
  connected: false,
  error: null,
  clipboard: null,

  loadAll: async () => {
    try {
      if (get().notebook) await get().flushSource()
      const [nb, files, settings] = await Promise.all([api.notebook(), api.files(), api.settings()])
      const keep = get().selectedId
      set({
        notebook: nb,
        files: files.files,
        settings,
        selectedId: keep && nb.cells.some((c) => c.id === keep) ? keep : nb.cells[0]?.id ?? null,
        error: null,
      })
      void get().syncSession(nb.name)
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) })
    }
  },

  setNotebook: (nb) => {
    const { editingId, notebook } = get()
    // Each notebook carries its own conversation and kernel; follow the file.
    if (nb.name) void get().syncSession(nb.name)
    if (nb.name && nb.name !== notebook?.name) void get().syncKernel(nb.name)
    const next = { ...nb }
    if (editingId && notebook) {
      const local = notebook.cells.find((c) => c.id === editingId)
      const idx = next.cells.findIndex((c) => c.id === editingId)
      if (local && idx >= 0) {
        next.cells = next.cells.map((c) => (c.id === editingId ? { ...c, source: local.source } : c))
      }
    }
    set({
      notebook: next,
      selectedId: get().selectedId && next.cells.some((c) => c.id === get().selectedId)
        ? get().selectedId
        : next.cells[0]?.id ?? null,
    })
  },

  select: (id) => set({ selectedId: id }),
  setEditing: (id) => set({ editingId: id }),

  updateLocalSource: (id, source) => {
    const nb = get().notebook
    if (!nb) return
    set({
      notebook: {
        ...nb,
        dirty: true,
        cells: nb.cells.map((c) => (c.id === id ? { ...c, source } : c)),
      },
    })
    pendingSources.set(id, source)
    if (patchTimer) window.clearTimeout(patchTimer)
    patchTimer = window.setTimeout(() => {
      void get().flushSource()
    }, 280)
  },

  flushSource: async (id) => {
    if (patchTimer) {
      window.clearTimeout(patchTimer)
      patchTimer = null
    }
    const entries = id ? [[id, pendingSources.get(id)]] : [...pendingSources.entries()]
    pendingSources.clear()
    for (const [cellId, source] of entries) {
      if (!cellId || source == null) continue
      try {
        await api.patchCell(String(cellId), { source: String(source) })
      } catch (err) {
        set({ error: err instanceof Error ? err.message : String(err) })
      }
    }
  },

  applyWs: (event) => {
    const type = event.type as string
    const nb = get().notebook
    // Every event names the notebook it belongs to. Cell and kernel traffic is
    // only meaningful for the document on screen; chat traffic is filed per
    // notebook so a background run stays intact until you switch back to it.
    const where = event.notebook ? String(event.notebook) : null
    const mine = !where || !nb || where === nb.name

    if (type === 'hello') {
      if (event.doc) get().setNotebook(event.doc as Notebook)
      if (event.kernel) set({ kernel: event.kernel as KernelStatus, connected: true })
      for (const name of (event.running_notebooks as string[]) || []) {
        editChat(set, get, name, (c) => ({ ...c, agent: { phase: 'running', detail: '' } }))
      }
      return
    }
    if (type === 'notebook') {
      if (event.doc && mine) get().setNotebook(event.doc as Notebook)
      return
    }
    if (type === 'kernel_status') {
      if (mine) set({ kernel: event.status as KernelStatus, connected: true })
      return
    }
    if (type === 'agent_status') {
      if (!where) return
      editChat(set, get, where, (c) => ({
        ...c,
        agent: { phase: String(event.phase || 'idle'), detail: String(event.detail || '') },
      }))
      return
    }
    if (type === 'agent_message') {
      if (!where) return
      editChat(set, get, where, (c) => ({
        ...c,
        messages: [
          ...c.messages,
          { id: uid(), role: event.role as ChatMessage['role'], content: String(event.content || '') },
        ],
      }))
      return
    }
    if (type === 'agent_delta') {
      const text = String(event.text || '')
      if (!text || !where) return
      editChat(set, get, where, (c) =>
        event.kind === 'reasoning'
          ? // Only the tail matters for a live trace; keeps the panel from ballooning.
            { ...c, live: { ...c.live, reasoning: (c.live.reasoning + text).slice(-360) } }
          : // First real answer token: the thinking trace has served its purpose.
            { ...c, live: { reasoning: '', content: c.live.content + text } }
      )
      return
    }
    if (type === 'agent_round_end') {
      if (!where) return
      editChat(set, get, where, (c) => {
        const content = String(event.content || '').trim() || c.live.content.trim()
        const reasoning = String(event.reasoning || '').trim()
        const next = { ...c, live: { reasoning: '', content: '' } }
        // Keep the thinking even when the round produced no prose of its own.
        if (!content && !reasoning) return next
        return { ...next, messages: [...c.messages, { id: uid(), role: 'assistant', content, reasoning }] }
      })
      return
    }
    if (!nb || !mine) return
    if (type === 'cell_running') {
      const cellId = String(event.cell_id)
      set({
        notebook: {
          ...nb,
          cells: nb.cells.map((c) =>
            c.id === cellId ? { ...c, status: 'running', outputs: [] } : c
          ),
        },
      })
      return
    }
    if (type === 'stream') {
      const cellId = String(event.cell_id)
      const name = String(event.name || 'stdout')
      const text = String(event.text || '')
      set({
        notebook: {
          ...nb,
          cells: nb.cells.map((c) => {
            if (c.id !== cellId) return c
            const outputs = [...(c.outputs || [])]
            const last = outputs[outputs.length - 1]
            if (last && last.output_type === 'stream' && last.name === name) {
              outputs[outputs.length - 1] = { ...last, text: last.text + text }
            } else {
              outputs.push({ output_type: 'stream', name, text })
            }
            return { ...c, outputs }
          }),
        },
      })
      return
    }
    if (type === 'output') {
      const cellId = String(event.cell_id)
      const output = event.output as Cell['outputs'][number]
      set({
        notebook: {
          ...nb,
          cells: nb.cells.map((c) =>
            c.id === cellId ? { ...c, outputs: [...(c.outputs || []), output] } : c
          ),
        },
      })
      return
    }
    if (type === 'cell_finished') {
      const cellId = String(event.cell_id)
      set({
        notebook: {
          ...nb,
          dirty: true,
          cells: nb.cells.map((c) =>
            c.id === cellId
              ? {
                  ...c,
                  status: event.status === 'ok' ? 'ok' : 'error',
                  outputs: (event.outputs as Cell['outputs']) || c.outputs,
                  execution_count: (event.execution_count as number | null) ?? c.execution_count,
                }
              : c
          ),
        },
      })
    }
  },

  setLeftOpen: (v) => {
    writeFlag('wello.leftOpen', v)
    set({ leftOpen: v })
  },
  setChatOpen: (v) => {
    writeFlag('wello.chatOpen', v)
    set({ chatOpen: v })
  },
  setSettings: (s) => set({ settings: s }),
  pushMessage: (msg) =>
    editChat(set, get, get().notebook?.name || '', (c) => ({ ...c, messages: [...c.messages, msg] })),
  setClipboard: (cell) => set({ clipboard: cell }),

  syncSession: async (name) => {
    // Live chats are kept in memory per notebook, so only a notebook this tab
    // has never seen (first visit, or after a reload) needs a read-back.
    if (!name || get().chats[name]?.loaded) return
    editChat(set, get, name, (c) => ({ ...c, loaded: true }))
    try {
      const session = await api.agentSession()
      if (session.notebook !== name) return
      editChat(set, get, name, (c) => ({
        ...c,
        // Deltas may already have arrived for this round; keep them at the end.
        messages: [
          ...session.messages.map((m) => ({
            id: uid(),
            role: m.role as ChatMessage['role'],
            content: m.content,
          })),
          ...c.messages,
        ],
        agent: session.running ? { phase: session.phase || 'running', detail: '' } : c.agent,
      }))
    } catch {
      /* an empty panel is fine; the next question starts a fresh context */
    }
  },

  syncKernel: async (name) => {
    // A kernel_status broadcast for the notebook we are switching to can land
    // before the switch itself, so read the status once we are showing it.
    try {
      const health = await api.health()
      if (get().notebook?.name !== name) return
      set({ kernel: health.kernel as KernelStatus })
    } catch {
      /* the next kernel event will correct it */
    }
  },

  clearContext: async () => {
    const name = get().notebook?.name || ''
    await api.resetChat()
    editChat(set, get, name, () => ({ ...EMPTY_CHAT, loaded: true }))
  },
}))

/** Update one notebook's chat, creating it on first sight. */
function editChat(
  set: (partial: Partial<AppState>) => void,
  get: () => AppState,
  name: string,
  fn: (chat: Chat) => Chat
) {
  if (!name) return
  const chats = get().chats
  set({ chats: { ...chats, [name]: fn(chats[name] ?? EMPTY_CHAT) } })
}

/** The conversation for a notebook, or an empty one with a stable identity. */
export function chatOf(state: AppState, name: string | null | undefined): Chat {
  return (name && state.chats[name]) || EMPTY_CHAT
}

export function isRunning(state: AppState, name: string): boolean {
  const phase = state.chats[name]?.agent.phase
  return !!phase && phase !== 'idle'
}

export async function runCell(cellId: string) {
  const { flushSource, notebook } = useApp.getState()
  await flushSource(cellId)
  const cell = notebook?.cells.find((c) => c.id === cellId)
  if (!cell || cell.cell_type !== 'code') return
  await api.executeCell(cellId)
}

export async function runAll(fromId?: string) {
  await useApp.getState().flushSource()
  await api.executeFrom(fromId, true)
}

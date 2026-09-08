async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = data.detail || JSON.stringify(data)
    } catch {
      detail = await res.text()
    }
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  health: () => request<{ ok: boolean; kernel: string }>('/api/health'),
  settings: () => request<import('./types').Settings>('/api/settings'),
  saveSettings: (body: Partial<import('./types').Settings>) =>
    request('/api/settings', { method: 'PUT', body: JSON.stringify(body) }),
  files: () => request<{ files: import('./types').FileInfo[] }>('/api/files'),
  deleteFile: (name: string) =>
    request<{
      files: import('./types').FileInfo[]
      notebook: import('./types').Notebook
      switched: boolean
    }>(`/api/files/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  importFiles: async (list: FileList | File[]) => {
    const form = new FormData()
    for (const f of Array.from(list)) form.append('files', f)
    const res = await fetch('/api/files/import', { method: 'POST', body: form })
    if (!res.ok) {
      let detail = res.statusText
      try {
        detail = (await res.json()).detail || detail
      } catch {
        /* keep statusText */
      }
      throw new Error(detail)
    }
    return (await res.json()) as {
      files: import('./types').FileInfo[]
      notebook: import('./types').Notebook
      imported: string[]
    }
  },
  notebook: () => request<import('./types').Notebook>('/api/notebook'),
  newNotebook: () => request<import('./types').Notebook>('/api/notebook/new', { method: 'POST' }),
  openNotebook: (name: string) =>
    request<import('./types').Notebook>('/api/notebook/open', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  saveNotebook: (name?: string) =>
    request<import('./types').Notebook>('/api/notebook/save', {
      method: 'POST',
      body: JSON.stringify({ name: name || null }),
    }),
  insertCell: (body: { index?: number; after_cell_id?: string; cell_type: string; source?: string }) =>
    request<import('./types').Cell>('/api/cells', { method: 'POST', body: JSON.stringify(body) }),
  patchCell: (id: string, body: { source?: string; cell_type?: string }) =>
    request(`/api/cells/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteCell: (id: string) => request(`/api/cells/${id}`, { method: 'DELETE' }),
  moveCell: (id: string, delta: number) =>
    request(`/api/cells/${id}/move`, { method: 'POST', body: JSON.stringify({ delta }) }),
  mergeCell: (id: string) => request(`/api/cells/${id}/merge`, { method: 'POST' }),
  clearOutputs: (id: string) => request(`/api/cells/${id}/clear`, { method: 'POST' }),
  clearAllOutputs: () => request<import('./types').Notebook>('/api/notebook/clear-outputs', { method: 'POST' }),
  executeCell: (id: string) => request(`/api/cells/${id}/execute`, { method: 'POST' }),
  executeFrom: (from_cell_id?: string, all = true) =>
    request('/api/execute', { method: 'POST', body: JSON.stringify({ from_cell_id, all }) }),
  interrupt: () => request('/api/kernel/interrupt', { method: 'POST' }),
  restart: () => request('/api/kernel/restart', { method: 'POST' }),
  chat: (message: string) => request('/api/agent/chat', { method: 'POST', body: JSON.stringify({ message }) }),
  stopAgent: (notebook?: string | null) =>
    request('/api/agent/stop', { method: 'POST', body: JSON.stringify({ notebook: notebook || null }) }),
  resetChat: () => request<{ ok: boolean; notebook: string }>('/api/agent/reset', { method: 'POST' }),
  agentSession: () =>
    request<{
      notebook: string
      messages: { role: string; content: string }[]
      running: boolean
      phase: string
      running_notebooks: string[]
    }>('/api/agent/session'),
}

export function exportUrl() {
  return '/api/notebook/export'
}

import { useApp } from './store'

let socket: WebSocket | null = null
let retry = 0
let timer: number | null = null

export function connectWs() {
  disconnectWs()
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws`)
  socket = ws
  ws.onopen = () => {
    retry = 0
    useApp.setState({ connected: true, error: null })
  }
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      useApp.getState().applyWs(data)
    } catch {
      /* ignore */
    }
  }
  ws.onclose = () => {
    useApp.setState({ connected: false })
    const wait = Math.min(8000, 600 * 2 ** retry)
    retry += 1
    timer = window.setTimeout(connectWs, wait)
  }
  ws.onerror = () => {
    ws.close()
  }
}

export function disconnectWs() {
  if (timer) {
    window.clearTimeout(timer)
    timer = null
  }
  if (socket) {
    socket.onclose = null
    socket.close()
    socket = null
  }
}

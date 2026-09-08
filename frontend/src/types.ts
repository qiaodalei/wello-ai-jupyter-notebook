export type CellType = 'code' | 'markdown'

export type CellStatus = 'idle' | 'running' | 'queued' | 'ok' | 'error'

export interface StreamOutput {
  output_type: 'stream'
  name: 'stdout' | 'stderr' | string
  text: string
}

export interface ErrorOutput {
  output_type: 'error'
  ename: string
  evalue: string
  traceback: string[]
}

export interface DisplayOutput {
  output_type: 'execute_result' | 'display_data'
  data: Record<string, string>
  metadata?: Record<string, unknown>
  execution_count?: number | null
}

export type CellOutput = StreamOutput | ErrorOutput | DisplayOutput

export interface Cell {
  id: string
  cell_type: CellType
  source: string
  outputs: CellOutput[]
  execution_count: number | null
  metadata: Record<string, unknown>
  status: CellStatus
}

export interface Notebook {
  name: string
  path: string
  dirty: boolean
  nbformat: number
  nbformat_minor: number
  metadata: Record<string, unknown>
  cells: Cell[]
}

export interface FileInfo {
  name: string
  path: string
  mtime: string
  size: number
}

export interface Settings {
  api_base: string
  api_key: string
  model: string
  auto_execute: boolean
  max_repair_rounds: number
  has_api_key?: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  /** The round's thinking, kept folded next to the answer. */
  reasoning?: string
}

export interface AgentStatus {
  phase: string
  detail: string
}

export type KernelStatus = 'starting' | 'idle' | 'busy' | 'dead'

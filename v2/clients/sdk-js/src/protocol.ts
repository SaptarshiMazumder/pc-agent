/**
 * Wire protocol types — the TS mirror of agentd/presentation/protocol.py and the payloads in
 * docs/PROTOCOL.md. Additive server fields are always allowed; clients ignore what they don't know.
 */

/** The protocol generation this SDK speaks (mirrors gateway.PROTOCOL_VERSION). */
export const PROTOCOL_VERSION = 1

export interface RequestFrame {
  type: 'req'
  id: string
  method: string
  params: Record<string, unknown>
}

export interface ResponseFrame {
  type: 'res'
  id: string
  ok: boolean
  payload: Record<string, any>
}

export interface EventFrame {
  type: 'event'
  event: string
  payload: Record<string, any>
}

export type Frame = ResponseFrame | EventFrame

/** An agent's app surface (present only for app agents — agents shipping their own UI). */
export interface AgentApp {
  title: string
  /** absolute path portion (e.g. "/apps/<id>/") — the opener appends its own token/scope */
  url: string
}

export interface AgentInfo {
  id: string
  name: string
  version?: string
  tagline?: string
  suggestions?: string[]
  color?: string
  /** null/absent for plain chat agents */
  app?: AgentApp | null
}

export interface Hello {
  agentName: string
  agentId: string
  model: string
  version: string
  protocol: number
  /** advisory in v1: false when this client declared a NEWER protocol than the server speaks */
  compatible?: boolean
  product: string
  productId: string
  storeEnabled: boolean
  registryConfigured: boolean
  registryUrl: string
  localRegistryDir: string
  workspace: string
  agents: AgentInfo[]
}

export interface SessionRow {
  sessionId: string
  title: string
  titleManual?: boolean
  snippet?: string
  projectId: string
  messages: number
  modified: number
  agentId: string
}

/** The inner event of a chat.event push: {type: "message_update" | "tool_execution_end" | ...}. */
export interface AgentEvent {
  type: string
  [key: string]: any
}

/** chat.event payload as broadcast by the daemon. */
export interface ChatEventPayload {
  sessionKey: string
  runId: string
  /** which agent the run belongs to (protocol v1 additive field) */
  agentId?: string
  ts: number
  event: AgentEvent
}

export interface CapabilityDescriptor {
  kind: 'tool' | 'plugin' | 'skill' | 'agent'
  id: string
  name: string
  description: string
  source: string
  extra: Record<string, unknown>
}

export interface InvokeResult {
  text: string
  artifacts: Array<Record<string, any>>
}

export interface SendResult {
  runId: string
  deduplicated?: boolean
  attachments?: Array<Record<string, any>>
}

export interface Attachment {
  name: string
  mimeType?: string
  dataBase64: string
}

/** The full text of a tool result (a message dict with content blocks). */
export function resultText(result: any): string {
  if (result && typeof result === 'object') {
    const content = result.content
    if (Array.isArray(content)) {
      return content
        .map((block: any) => (block && typeof block === 'object' ? block.text || '' : ''))
        .join('')
        .trim()
    }
    return String(result.text || '').trim()
  }
  return String(result ?? '').trim()
}

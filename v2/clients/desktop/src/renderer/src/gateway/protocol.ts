/**
 * Wire protocol types — the TS mirror of agentd/presentation/protocol.py and the
 * event payloads the daemon broadcasts (see the terminal client for the reference
 * rendering of each event type).
 */

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

export interface AgentInfo {
  id: string
  name: string
  version?: string
}

export interface Hello {
  agentName: string
  agentId: string
  model: string
  version: string
  protocol: number
  product: string
  productId: string
  storeEnabled: boolean
  registryConfigured: boolean
  workspace: string
  agents: AgentInfo[]
}

export interface SessionRow {
  sessionId: string
  title: string
  titleManual: boolean
  messages: number
  modified: number
}

/** chat.event payload: {sessionKey, runId, event: {type, ...}} */
export interface AgentEvent {
  type: string
  [key: string]: any
}

export interface CatalogBundle {
  id: string
  name: string
  version: string
  description: string
  price: string
  size: number
  compatible: boolean
  installed: boolean
  installedVersion: string
  updateAvailable: boolean
  entitlement: string
}

export interface InstalledBundle {
  id: string
  version: string
  installedAt: string
  pluginIds: string[]
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

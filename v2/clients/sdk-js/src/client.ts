/**
 * AgentdClient — one WebSocket to an agentd daemon: promise-based requests (id-matched res
 * frames) + fan-out of broadcast events, with auto-reconnect. Extracted from the desktop
 * client's proven GatewayClient and published as the ONE way to speak the protocol.
 *
 * Transport-agnostic by contract: you hand it a URL + token (local ws:// today, hosted wss://
 * tomorrow) — the SDK itself never discovers, assumes, or prefers any location.
 *
 * Runs anywhere a WHATWG WebSocket exists: browsers (agent apps), Electron, Node >= 21.
 */

import {
  PROTOCOL_VERSION,
  type AgentInfo,
  type Attachment,
  type CapabilityDescriptor,
  type ChatEventPayload,
  type Frame,
  type Hello,
  type InvokeResult,
  type RequestFrame,
  type SendResult,
  type SessionRow
} from './protocol'
import { effectiveMode, loadSession } from './session'

export type ConnectionStatus = 'connecting' | 'open' | 'closed'
type EventHandler = (payload: Record<string, any>) => void
type StatusHandler = (status: ConnectionStatus) => void

interface Pending {
  resolve: (payload: Record<string, any>) => void
  reject: (error: Error) => void
}

/** Where and how to connect. `url` may be ws(s):// or http(s):// (auto-upgraded to ws). */
export interface ConnectTarget {
  url: string
  /** The MACHINE token — may this client connect at all. */
  token?: string
  /** The SESSION token — WHO is connecting. Two credentials, two jobs: `token` is a machine
   *  secret, `session` identifies a person. A hosted daemon has no machine token and the session
   *  does both, which is why the server falls back to `token` when this is absent. */
  session?: string
  /** WHICH KEYS pay for this connection's model calls: 'local' or 'cloud'. A preference, never a
   *  credential — the daemon pays with the session above, so a client can only bill itself. */
  mode?: string
  /** app connections: restrict this connection to one agent (stable tier only) */
  scope?: string
}

/** Static target, or a resolver called on EVERY (re)connect — so a host that can re-discover
 *  a restarted daemon (new port/token) hands a function; a browser app hands its fixed URL. */
export type ConnectInput = ConnectTarget | (() => Promise<ConnectTarget>)

export interface AgentdClientOptions {
  /** identifies this client in hello (e.g. "my-app/1.0"); helps server-side observability */
  clientName?: string
}

function toWsUrl(target: ConnectTarget): string {
  const u = new URL(target.url)
  if (u.protocol === 'http:') u.protocol = 'ws:'
  if (u.protocol === 'https:') u.protocol = 'wss:'
  if (target.token) u.searchParams.set('token', target.token)
  if (target.session) u.searchParams.set('session', target.session)
  if (target.mode) u.searchParams.set('mode', target.mode)
  if (target.scope) u.searchParams.set('scope', target.scope)
  return u.toString()
}

function toHttpOrigin(wsUrl: string): string {
  const u = new URL(wsUrl)
  u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:'
  u.search = ''
  u.pathname = ''
  return u.origin
}

export class AgentdClient {
  private ws: WebSocket | null = null
  private input: ConnectInput | null = null
  private nextId = 1
  private pending = new Map<string, Pending>()
  private eventHandlers = new Map<string, Set<EventHandler>>()
  private statusHandlers = new Set<StatusHandler>()
  private reconnectDelay = 1000
  private closedByUs = false
  private lastTarget: ConnectTarget | null = null
  private readonly clientName: string

  constructor(options: AgentdClientOptions = {}) {
    this.clientName = options.clientName || `@agentd/client/${PROTOCOL_VERSION}`
  }

  /** Connect (or switch) to a daemon. Reconnects automatically with backoff until close(). */
  connect(input: ConnectInput): void {
    this.input = input
    this.closedByUs = false
    void this.open()
  }

  close(): void {
    this.closedByUs = true
    this.teardownSocket()
  }

  /** Re-open the socket, re-reading the target.
   *
   *  Identity and run mode are read by the daemon when a connection OPENS, so changing either
   *  has to bring up a new one — otherwise the daemon goes on answering as whoever this client
   *  was before. Called by authLogin / authLogout / setRunMode. */
  reconnect(): void {
    if (!this.input) return
    this.closedByUs = false
    void this.open()
  }

  get connected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  private scheduleReconnect(): void {
    if (this.closedByUs) return
    setTimeout(() => void this.open(), this.reconnectDelay)
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 15_000)
  }

  private async open(): Promise<void> {
    if (!this.input) return
    // Tear down any existing socket FIRST so a re-connect never leaves two live sockets
    // both fanning out events (double-render bug in strict-mode UIs).
    this.teardownSocket()
    for (const handler of this.statusHandlers) handler('connecting')
    let target: ConnectTarget
    try {
      target = typeof this.input === 'function' ? await this.input() : this.input
    } catch {
      this.scheduleReconnect() // daemon momentarily unreachable — back off and retry
      return
    }
    this.lastTarget = target
    const ws = new WebSocket(toWsUrl(target))
    this.teardownSocket() // close any socket a concurrent open() just assigned
    this.ws = ws
    ws.onopen = () => {
      this.reconnectDelay = 1000
      for (const handler of this.statusHandlers) handler('open')
    }
    ws.onmessage = (message) => this.handleFrame(JSON.parse(message.data as string) as Frame)
    ws.onclose = () => {
      for (const [, pending] of this.pending) pending.reject(new Error('connection closed'))
      this.pending.clear()
      for (const handler of this.statusHandlers) handler('closed')
      this.scheduleReconnect()
    }
  }

  /** Detach + close the current socket without triggering its reconnect. */
  private teardownSocket(): void {
    const old = this.ws
    if (!old) return
    this.ws = null
    old.onopen = null
    old.onmessage = null
    old.onclose = null
    old.onerror = null
    try {
      old.close()
    } catch {
      /* already closing/closed */
    }
  }

  // ------------------------------------------------------------------ raw protocol

  request<T = Record<string, any>>(
    method: string,
    params: Record<string, unknown> = {}
  ): Promise<T> {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('not connected'))
    }
    const id = String(this.nextId++)
    const frame: RequestFrame = { type: 'req', id, method, params }
    ws.send(JSON.stringify(frame))
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as Pending['resolve'], reject })
    })
  }

  /** Subscribe to a broadcast event by name. Returns the unsubscribe. */
  on(event: string, handler: EventHandler): () => void {
    if (!this.eventHandlers.has(event)) this.eventHandlers.set(event, new Set())
    this.eventHandlers.get(event)!.add(handler)
    return () => this.eventHandlers.get(event)?.delete(handler)
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler)
    return () => this.statusHandlers.delete(handler)
  }

  private handleFrame(frame: Frame): void {
    if (frame.type === 'res') {
      const pending = this.pending.get(frame.id)
      if (!pending) return
      this.pending.delete(frame.id)
      if (frame.ok) pending.resolve(frame.payload || {})
      else pending.reject(new Error(String(frame.payload?.error || 'gateway error')))
    } else if (frame.type === 'event') {
      for (const handler of this.eventHandlers.get(frame.event) || []) {
        handler(frame.payload || {})
      }
    }
  }

  // ------------------------------------------------------------------ typed helpers

  /** Handshake — introduces this client + its protocol so the server can flag compatibility. */
  hello(): Promise<Hello> {
    return this.request<Hello>('hello', { protocol: PROTOCOL_VERSION, client: this.clientName })
  }

  async agents(): Promise<{ agents: AgentInfo[]; default: string }> {
    return this.request('agents.list')
  }

  agentDetail(agentId: string): Promise<Record<string, any>> {
    return this.request('agents.detail', { agentId })
  }

  sessions(agentId?: string): Promise<{ sessions: SessionRow[] }> {
    return this.request('sessions.list', agentId ? { agentId } : {})
  }

  history(sessionKey: string, agentId?: string): Promise<{ messages: any[] }> {
    return this.request('sessions.history', { sessionKey, ...(agentId ? { agentId } : {}) })
  }

  send(opts: {
    message: string
    sessionKey?: string
    agentId?: string
    projectId?: string
    attachments?: Attachment[]
    idempotencyKey?: string
  }): Promise<SendResult> {
    return this.request<SendResult>('chat.send', { sessionKey: 'default', ...opts })
  }

  abort(sessionKey: string): Promise<{ aborted: boolean; runId?: string }> {
    return this.request('chat.abort', { sessionKey })
  }

  invokeTool(name: string, params: Record<string, unknown> = {}): Promise<InvokeResult> {
    return this.request<InvokeResult>('tools.invoke', { name, params })
  }

  capabilities(agentId?: string): Promise<{ capabilities: CapabilityDescriptor[] }> {
    return this.request('capabilities.list', agentId ? { agentId } : {})
  }

  catalog(): Promise<Record<string, any>> {
    return this.request('plugins.catalog')
  }

  notifications(): Promise<Record<string, any>> {
    return this.request('notifications.list')
  }

  /**
   * Follow ONE session's run events (the daemon broadcasts every session's events to every
   * authorized socket — this does the filtering bookkeeping for you). Returns the unsubscribe.
   */
  onRun(sessionKey: string, handler: (payload: ChatEventPayload) => void): () => void {
    return this.on('chat.event', (payload) => {
      if ((payload as ChatEventPayload).sessionKey === sessionKey) {
        handler(payload as ChatEventPayload)
      }
    })
  }

  /** Follow every run of ONE agent (uses the protocol-v1 agentId event field). */
  onAgent(agentId: string, handler: (payload: ChatEventPayload) => void): () => void {
    return this.on('chat.event', (payload) => {
      if ((payload as ChatEventPayload).agentId === agentId) {
        handler(payload as ChatEventPayload)
      }
    })
  }

  /** Build the authenticated GET /file URL for a server-side artifact path. */
  fileUrl(path: string): string {
    if (!this.lastTarget) throw new Error('not connected')
    const origin = toHttpOrigin(new URL(this.lastTarget.url).toString())
    const u = new URL('/file', origin)
    u.searchParams.set('path', path)
    if (this.lastTarget.token) u.searchParams.set('token', this.lastTarget.token)
    return u.toString()
  }
}

/**
 * Convenience for agent apps served by the daemon at /apps/<id>/: the opener put token+scope
 * in the page URL and the WS shares the page's own origin — so an app connects with one line:
 *   const client = agentd.fromPage()
 */
export function fromPage(options: AgentdClientOptions = {}): AgentdClient {
  const here = new URL(window.location.href)
  const token = here.searchParams.get('token') || ''
  const scope = here.searchParams.get('scope') || ''
  // THE OPENER'S IDENTITY AND MODE, when it passed them. A desktop shell that is signed in
  // launches its app windows with `?session=&mode=` so the window bills and acts as the same
  // person — without this, every app window of a signed-in user ran ANONYMOUS (no cloud
  // billing, model calls falling to dead BYOK keys). Signing in INSIDE the app still wins:
  // that is this window's own, newer answer, and it is what a re-login here must override.
  const urlSession = here.searchParams.get('session') || ''
  const urlMode = here.searchParams.get('mode') || ''
  const client = new AgentdClient(options)
  // The RESOLVER form, not a static target: the stored session and mode are re-read on every
  // (re)connect, so a sign-in or a mode change is carried by the next socket without the app
  // doing anything. A fixed object would pin whatever was stored at page load.
  client.connect(async () => {
    const stored = loadSession()?.token
    return {
      url: here.origin,
      token: token || undefined,
      session: stored || urlSession || undefined,
      mode: urlMode || effectiveMode('', !!(stored || urlSession)),
      scope: scope || undefined
    }
  })
  return client
}

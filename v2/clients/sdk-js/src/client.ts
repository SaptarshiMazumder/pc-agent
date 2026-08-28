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
import {
  accessTokenExpiry,
  effectiveMode,
  loadSession,
  saveMode,
  saveSession,
  type RunMode
} from './session'
import { identity } from './identity'

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
  /** The last status announced — see `onStatus` for why this is remembered. */
  private status: ConnectionStatus = 'connecting'
  private reconnectDelay = 1000
  /** When the current socket opened, so "did this connection actually work?" can be answered. */
  private openedAt = 0
  //: Backoff ceiling for a credential the server REFUSED. Retrying a dead token fast is not
  //: resilience, it is a flood — and the server is the thing being flooded.
  private static readonly UNAUTHORIZED_DELAY = 60_000
  //: A socket must survive this long before it counts as a working connection.
  private static readonly HEALTHY_MS = 10_000
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
    this.status = 'connecting'
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
      // DELIBERATELY NOT resetting the backoff here. A WebSocket upgrade succeeding says nothing
      // about whether the connection is USABLE: the daemon accepts the upgrade and only then
      // closes with 4401 when the credential is refused. Resetting on open therefore made every
      // rejected attempt look like a success, pinning the delay at its minimum and turning one
      // stale token into a steady flood against our own daemon. The reset moved to onclose,
      // where the socket's lifetime is known.
      this.openedAt = Date.now()
      this.status = 'open'
    for (const handler of this.statusHandlers) handler('open')
    }
    ws.onmessage = (message) => this.handleFrame(JSON.parse(message.data as string) as Frame)
    ws.onclose = (event) => {
      for (const [, pending] of this.pending) pending.reject(new Error('connection closed'))
      this.pending.clear()
      this.status = 'closed'
    for (const handler of this.statusHandlers) handler('closed')
      const lived = this.openedAt ? Date.now() - this.openedAt : 0
      this.openedAt = 0
      // 4401 is the daemon refusing the credential (see gateway `_handle_conn`). Reconnecting
      // cannot fix that — only signing in again can — so go straight to the ceiling instead of
      // asking the same question every second.
      if (event && (event as CloseEvent).code === 4401) {
        this.reconnectDelay = AgentdClient.UNAUTHORIZED_DELAY
      } else if (lived >= AgentdClient.HEALTHY_MS) {
        this.reconnectDelay = 1000 // a connection that actually served is what earns a reset
      }
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

  /**
   * Subscribe to connection status. Returns the unsubscribe.
   *
   * THE CURRENT STATUS ARRIVES IMMEDIATELY, before this returns. Status was transitions-only, and
   * a subscriber that mounted after the socket opened — which is most of them, since connecting
   * starts at construction and React mounts a frame later — heard nothing until the next
   * reconnect. The symptom is a composer that says "connecting…" and refuses to send over a
   * perfectly open socket.
   */
  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler)
    handler(this.status)
    return () => this.statusHandlers.delete(handler)
  }

  private handleFrame(frame: Frame): void {
    if (frame.type === 'res') {
      const pending = this.pending.get(frame.id)
      if (!pending) return
      this.pending.delete(frame.id)
      if (frame.ok) pending.resolve(frame.payload || {})
      else {
        // The MESSAGE is for a human; the CODE is for code. auth.update's caller has to tell "this
        // token can never work" (reconnect and re-gate) from "the daemon hiccuped" (do nothing) —
        // and matching on prose strings is how that distinction silently breaks on a reword.
        const err = new Error(String(frame.payload?.error || 'gateway error')) as Error & {
          code?: string
        }
        if (typeof frame.payload?.code === 'string') err.code = frame.payload.code
        pending.reject(err)
      }
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
    // BOTH, when we have both — not token-or-session.
    //
    // The daemon mints a NEW machine token every time it starts, and a window's token is fixed
    // at the moment it opened. So after any daemon restart the token this client holds is dead,
    // while its SESSION is still perfectly good — the socket proves it by reconnecting on the
    // same credentials. Sending only the token turned that into a 401 on every /file URL in a
    // signed-in window: images stopped loading and the reason was invisible, because nothing
    // about a stale token looks different from a wrong one.
    //
    // The daemon reads `session or token`, so sending both means the live credential wins and
    // the dead one is simply ignored.
    if (this.lastTarget.token) u.searchParams.set('token', this.lastTarget.token)
    if (this.lastTarget.session) u.searchParams.set('session', this.lastTarget.session)
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
  // A page reached from a marketplace card is a BARE /apps/<id>/ url — no `?scope=`. The path
  // still says which agent this is, and without this fallback such a page opened an UNSCOPED
  // (host-tier) connection: agentId-less reads defaulted to MAIN (a figure app rendering the
  // user's JARVIS history, found live), and third-party app code held the full host method
  // surface. Same derivation the session storage key already uses (session.ts).
  const pathAgent = /\/apps\/([^/]+)/.exec(here.pathname)
  const scope =
    here.searchParams.get('scope') ||
    (pathAgent ? `agent:${decodeURIComponent(pathAgent[1])}` : '')
  // THE OPENER'S IDENTITY AND MODE, when it passed them, are ADOPTED ONCE: saved into this
  // window's own storage and removed from the address bar. After this block there is exactly
  // ONE source of truth — the stored session — which every later sign-in, sign-out and
  // reconnect naturally owns. The url params must not survive as a competing source: kept in
  // the resolver they either lost to a STALE stored session (an opener's fresh identity
  // silently ignored — found live: a freshly signed-up user's app ran as the machine's
  // previous tester) or, ordered the other way, they would override every in-app re-login
  // for the life of the window. Adoption is what makes both orderings moot.
  const urlSession = here.searchParams.get('session') || ''
  const urlMode = here.searchParams.get('mode') || ''
  // `expiresAt` is stamped from the token's own `exp`, so the window knows when its borrowed
  // credential runs out. Without it the page kept presenting a dead token and the daemon accepted
  // the reconnect ANONYMOUSLY — signed in by its own account, invisible to the user (see `spent`
  // in session.ts). An opener-supplied session carries NO refresh token, deliberately: this window
  // runs third-party code and must never hold a 30-day credential for the whole account.
  if (urlSession) {
    saveSession({
      token: urlSession,
      email: '',
      accountId: '',
      expiresAt: accessTokenExpiry(urlSession) || undefined
    })
  }
  if (urlMode === 'local' || urlMode === 'cloud') saveMode(urlMode as RunMode)
  if ((urlSession || urlMode) && typeof history !== 'undefined') {
    here.searchParams.delete('session')
    here.searchParams.delete('mode')
    history.replaceState(null, '', here.toString())
  }
  const client = new AgentdClient(options)
  // The RESOLVER form, not a static target: identity and mode are re-read on every (re)connect,
  // so a sign-in or a mode change is carried by the next socket without the app doing anything.
  //
  // On DESKTOP nothing travels: the daemon inherits the machine's identity for a connection that
  // presents no session (gateway.py). The one thing the window must still know is WHETHER the
  // machine is signed in, because the default run mode hangs on it — so it asks the runtime,
  // whose answer is cached and shared with everything else via `identity()`. On HOSTED that ask
  // is a 404 (answered as signed-out) and the borrowed launch-URL session travels instead.
  client.connect(async () => {
    const stored = loadSession()?.token
    const signedIn = !!stored || (await identity({ origin: here.origin }).state()).state === 'ok'
    return {
      url: here.origin,
      token: token || undefined,
      session: stored || undefined,
      mode: effectiveMode('', signedIn),
      scope: scope || undefined
    }
  })
  return client
}

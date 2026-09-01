/**
 * GatewayClient — one WebSocket to the daemon: promise-based requests (id-matched
 * res frames) + fan-out of broadcast events. Auto-reconnects with backoff when the
 * daemon restarts; the store re-runs its handshake on every (re)open.
 */

import type { Frame, RequestFrame } from './protocol'

type EventHandler = (payload: Record<string, any>) => void
type StatusHandler = (status: 'connecting' | 'open' | 'closed') => void

interface Pending {
  resolve: (payload: Record<string, any>) => void
  reject: (error: Error) => void
}

/** Resolves the current connect URL (host+port+token). Called on EVERY (re)connect,
 *  so a daemon restart — which rotates the auth token and can change the port — is
 *  handled transparently instead of the client looping on a stale, now-rejected URL. */
type UrlProvider = () => Promise<string>

export class GatewayClient {
  private ws: WebSocket | null = null
  private urlProvider: UrlProvider | null = null
  private nextId = 1
  private pending = new Map<string, Pending>()
  private eventHandlers = new Map<string, Set<EventHandler>>()
  private statusHandlers = new Set<StatusHandler>()
  private reconnectDelay = 1000
  private closedByUs = false

  connect(urlProvider: UrlProvider): void {
    this.urlProvider = urlProvider
    this.closedByUs = false
    void this.open()
  }

  /** Re-dial NOW, re-resolving the url. The credential is IN that url (`?session=`), so a
   *  sign-in or sign-out only reaches the daemon when the socket is rebuilt — waiting out the
   *  backoff would leave the app authenticated in the UI and anonymous on the wire. */
  reconnect(): void {
    if (!this.urlProvider || this.closedByUs) return
    this.reconnectDelay = 1000
    void this.open()
  }

  private scheduleReconnect(): void {
    if (this.closedByUs) return
    setTimeout(() => void this.open(), this.reconnectDelay)
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 15_000)
  }

  private async open(): Promise<void> {
    if (!this.urlProvider) return
    // Tear down any existing socket FIRST so a re-connect never leaves two live sockets
    // both fanning out events. Without this, React StrictMode's double-mount (dev) calls
    // connect() twice and every chat event would be handled — and rendered — twice.
    this.teardownSocket()
    for (const handler of this.statusHandlers) handler('connecting')
    let url: string
    try {
      url = await this.urlProvider() // re-resolves host/port/token every time (daemon may have restarted)
    } catch {
      this.scheduleReconnect() // daemon momentarily unreachable — back off and retry
      return
    }
    const ws = new WebSocket(url)
    this.teardownSocket() // close any socket a concurrent open() (StrictMode) just assigned
    this.ws = ws
    ws.onopen = () => {
      for (const handler of this.statusHandlers) handler('open')
    }
    ws.onmessage = (message) => {
      // The backoff resets HERE, not in onopen. A socket that opens and then dies before a
      // single frame has NOT proven anything — resetting on open meant an open-then-drop
      // failure redialled at 1s forever, and a page full of those is a self-inflicted
      // connection storm. One received frame is the connection earning its reset.
      this.reconnectDelay = 1000
      this.handleFrame(JSON.parse(message.data as string) as Frame)
    }
    ws.onclose = () => {
      for (const [, pending] of this.pending) pending.reject(new Error('connection closed'))
      this.pending.clear()
      for (const handler of this.statusHandlers) handler('closed')
      this.scheduleReconnect()
    }
  }

  /** Detach + close the current socket without triggering its reconnect. Used before
   *  opening a new one (idempotent connect) and on explicit close. */
  private teardownSocket(): void {
    const old = this.ws
    if (!old) return
    this.ws = null
    old.onopen = null
    old.onmessage = null
    old.onclose = null // so its close doesn't schedule a reconnect
    old.onerror = null
    try {
      old.close()
    } catch {
      /* already closing/closed */
    }
  }

  close(): void {
    this.closedByUs = true
    this.teardownSocket()
  }

  request<T = Record<string, any>>(method: string, params: Record<string, unknown> = {}): Promise<T> {
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

  /** Subscribe to a broadcast event (chat.event, notification, marketplace.progress,
   *  agents.changed). Returns the unsubscribe. */
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
}

export const gateway = new GatewayClient()

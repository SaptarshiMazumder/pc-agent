/**
 * Real-user monitoring — what the BROWSER knows and the server cannot (plan item 5.3).
 *
 * Every other signal in this system is measured server-side, which is why Phases 0-3 needed no
 * client code at all. But a whole class of failure is invisible from there: the page that never
 * finished loading, the JavaScript exception that left the composer dead, the WebSocket that
 * reconnects every ninety seconds on someone's hotel wifi, and the gap between "the proxy
 * answered in 900ms" and "the user saw a word after four seconds". None of those produce a
 * server-side symptom. Each is a reason someone quietly stops using the product.
 *
 * SAME MAIL SLOT AS THE DESKTOP. Posts to the ingest service's /v1/events, whose allowlist
 * decides what may be published — so a bug here cannot invent a metric or a dimension, and the
 * browser is treated as exactly as untrusted as it is.
 *
 * WHAT IT DELIBERATELY DOES NOT SEND. No message text, no URLs beyond the origin, no error
 * MESSAGES (a thrown Error routinely stringifies a user's content into its message), no stack
 * traces, no user agent. An error is reported as "an error happened", counted by nothing more
 * specific than that. The name of the failing file is not worth the risk of shipping a chat
 * message inside an exception string.
 *
 * INERT BY DEFAULT. No ingest URL configured means no listeners are installed and no socket is
 * ever opened, and on DESKTOP it never runs at all — the daemon's own uploader covers that
 * surface, opt-in, and a second reporter would double-count the same runs.
 */

import { getSession } from './auth'
import { isDesktop } from './platform'

type Event = { name: string; value?: number; outcome?: string; reason?: string }

/** Where reports go. Query param (local testing) beats the baked build value. */
function ingestUrl(): string {
  const q = new URLSearchParams(typeof location !== 'undefined' ? location.search : '')
  const env = (import.meta as { env?: Record<string, string> }).env || {}
  return (q.get('ingest') || env.VITE_AGENTD_INGEST_URL || '').replace(/\/$/, '')
}

const FLUSH_MS = 30_000
/** Bounded, like the desktop buffer: a tab left open for a week must not grow without limit. */
const MAX_QUEUED = 100

let queue: Event[] = []
let dropped = 0
let started = false
let timer: ReturnType<typeof setInterval> | null = null

function push(event: Event): void {
  if (!started) return
  if (queue.length >= MAX_QUEUED) {
    dropped += 1
    queue.shift() // keep the RECENT events; they are the ones that explain the current state
  }
  queue.push(event)
}

function flush(useBeacon = false): void {
  const url = ingestUrl()
  if (!url || queue.length === 0) return
  const body = JSON.stringify({ surface: 'web', events: queue, dropped })
  queue = []
  dropped = 0

  // sendBeacon survives the page being closed, which is the ONLY way the last events of a
  // session are ever delivered — a normal fetch is cancelled on unload. It cannot carry an
  // Authorization header, so those events arrive anonymous; that is an acceptable trade for
  // having them at all, and the server treats anonymous events as first-class.
  if (useBeacon && typeof navigator !== 'undefined' && navigator.sendBeacon) {
    try {
      navigator.sendBeacon(`${url}/v1/events`, new Blob([body], { type: 'application/json' }))
      return
    } catch {
      /* fall through to fetch */
    }
  }

  const session = getSession()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (session) headers.Authorization = `Bearer ${session.token}`
  // keepalive so an in-flight batch is not cancelled by a navigation mid-send.
  void fetch(`${url}/v1/events`, { method: 'POST', headers, body, keepalive: true }).catch(() => {
    /* telemetry must never surface an error to the user, and must never retry into a storm */
  })
}

// --------------------------------------------------------------------------- public API

/** A run finished in the UI. `firstTokenMs` is PERCEIVED latency — send to first visible text. */
export function reportRun(outcome: string, firstTokenMs?: number): void {
  push({ name: 'web_run_total', value: 1, outcome })
  if (typeof firstTokenMs === 'number' && firstTokenMs > 0) {
    push({ name: 'web_first_token_ms', value: Math.round(firstTokenMs), outcome })
  }
}

/** The WebSocket dropped and is reconnecting. A rising rate is a bad ALB idle timeout, a proxy
 *  killing idle connections, or a daemon restarting — all invisible server-side, because each
 *  individual reconnect looks like a normal new connection. */
export function reportReconnect(): void {
  push({ name: 'web_ws_reconnect_total', value: 1 })
}

/**
 * Install the browser listeners. Idempotent; safe to call from an effect.
 *
 * Returns a teardown so a hot reload does not stack duplicate listeners.
 */
export function installRum(): () => void {
  // Desktop has its own opt-in uploader in the daemon; a second reporter here would double-count
  // and would bypass the consent toggle the user set.
  if (started || isDesktop || !ingestUrl()) return () => {}
  started = true

  const onError = (): void => push({ name: 'web_error_total', value: 1, reason: 'crash' })
  const onRejection = (): void => push({ name: 'web_error_total', value: 1, reason: 'unknown' })
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onRejection)

  // Page load, from the navigation entry rather than a timer we started — this counts the time
  // BEFORE our JavaScript ran, which is most of it on a cold cache.
  try {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    if (nav && nav.duration > 0) push({ name: 'web_page_load_ms', value: Math.round(nav.duration) })
  } catch {
    /* an old browser without the navigation timing API is not worth a branch */
  }

  timer = setInterval(() => flush(), FLUSH_MS)
  // 'pagehide' rather than 'unload': it is the only one that fires reliably on mobile Safari and
  // when a tab is restored from the back/forward cache.
  const onHide = (): void => flush(true)
  window.addEventListener('pagehide', onHide)

  return () => {
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onRejection)
    window.removeEventListener('pagehide', onHide)
    if (timer) clearInterval(timer)
    timer = null
    started = false
  }
}

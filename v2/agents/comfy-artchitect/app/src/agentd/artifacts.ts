/* Artifacts — media and documents an agent PRODUCED, detected server-side and carried on chat
 * events and history messages.
 *
 * WHY THIS IS SHORTER THAN agentd's. That client is an Electron shell that has to work out where
 * the daemon lives (`setGatewayUrl` parses the resolved ws URL for an origin and a token, and
 * re-parses it on every reconnect, so a daemon restart on a new port just works). This window is
 * SERVED BY the daemon — the page's own origin is the daemon's origin, and the token it was opened
 * with is in its own URL. There is nothing to discover and nothing to keep in step.
 */

import { identity, loadSession, onIdentityChanged } from '@agentd/client'

export type ArtifactKind = 'image' | 'video' | 'audio' | 'file'

export interface Artifact {
  path: string
  name: string
  mime: string
  kind: ArtifactKind
  size?: number
}

/** The MACHINE token this window was opened with, when there is one. Present on a desktop
 *  daemon; absent on a hosted deployment, where identity is a person rather than a machine. */
const TOKEN = new URL(location.href).searchParams.get('token') || ''

/** Absolute URL to stream one artifact's bytes from the daemon's guarded /file endpoint.
 *
 *  IT MUST CARRY THE SESSION, NOT JUST THE MACHINE TOKEN. `/file` resolves the caller exactly as
 *  the socket does — `?session=` first, `?token=`/Bearer as the fallback — and on a hosted
 *  deployment only the session identifies anybody. Two things then conspire: a hosted window is
 *  opened with `?session=`, never `?token=`, and the client STRIPS that param from the address
 *  bar once it has stored it (client.ts), so reading `location.href` here finds nothing. The
 *  app-session cookie does not rescue it either — it is path-scoped to `/apps/<id>/` and never
 *  travels to `/file`. So every artifact fetch went out with no credential at all and came back
 *  401: images rendered blank and the file viewer showed "could not read this file: HTTP 401".
 *
 *  `loadSession()` is the SDK's own store — the same value the socket connects with, read
 *  synchronously so an `<img src>` can use it too. */
export function fileUrl(path: string): string {
  const q = new URLSearchParams({ path })
  let session = live
  if (!session) {
    try {
      session = loadSession()?.token || ''
    } catch {
      /* no storage (private window) — fall through to the machine token */
    }
  }
  if (session) q.set('session', session)
  if (TOKEN) q.set('token', TOKEN)
  return `${location.origin}/file?${q.toString()}`
}

/** The freshest access token this window has seen, kept for `fileUrl` to read SYNCHRONOUSLY.
 *
 *  The stored session cannot be trusted for long: it is written ONCE, at boot, from the launch
 *  URL, and nothing refreshes it (client.ts) — so an hour into a session every image and every
 *  file fetch would start 401ing again. `identity()` does hold a live, auto-renewing token, but
 *  only behind a promise, and `<img src>` cannot await. So the live value is mirrored here as it
 *  changes, and the stored one remains the fallback for the first render. */
let live = ''

/** Start keeping `fileUrl`'s credential current. Idempotent; call it once at boot. */
export function primeFileAuth(): () => void {
  const pull = () => {
    void identity({})
      .accessToken()
      .then((t) => {
        if (t) live = t
      })
      .catch(() => {
        /* unreachable identity leaves the previous token in place — it may still be valid */
      })
  }
  pull()
  // A sign-in, a sign-out or an account switch changes which files this window may even read.
  const off = onIdentityChanged(pull)
  // And a quiet window still ages out of its token, so re-read on a slow beat. Cheap: the
  // fetcher caches and single-flights, so this is a memory read until the token nears expiry.
  const timer = setInterval(pull, 4 * 60 * 1000)
  return () => {
    off()
    clearInterval(timer)
  }
}

export function humanSize(bytes?: number): string {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let n = bytes
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i++
  }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`
}

/** Artifacts off a wire payload, ignoring anything that is not shaped like one. Model-adjacent
 *  data: a malformed entry should render nothing rather than a card of `undefined`. */
export function readArtifacts(raw: unknown): Artifact[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((a: any) => {
    const path = String(a?.path || '')
    if (!path) return []
    const kind = String(a?.kind || 'file')
    return [
      {
        path,
        name: String(a?.name || path.split(/[\\/]/).pop() || 'file'),
        mime: String(a?.mime || ''),
        kind: (['image', 'video', 'audio', 'file'].includes(kind) ? kind : 'file') as ArtifactKind,
        size: Number(a?.size) || undefined,
      },
    ]
  })
}

/** Incoming artifacts not already waiting — dedupes WITHIN a run, so a model declaring the same
 *  file twice shows it once, while a later turn may still re-present a file you asked to see. */
export function freshArtifacts(have: Artifact[], incoming: Artifact[]): Artifact[] {
  const seen = new Set(have.map((a) => a.path))
  return incoming.filter((a) => !seen.has(a.path))
}

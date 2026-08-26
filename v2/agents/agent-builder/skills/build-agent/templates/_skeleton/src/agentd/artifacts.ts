/* Artifacts — media and documents an agent PRODUCED, detected server-side and carried on chat
 * events and history messages.
 *
 * WHY THIS IS SHORTER THAN agentd's. That client is an Electron shell that has to work out where
 * the daemon lives (`setGatewayUrl` parses the resolved ws URL for an origin and a token, and
 * re-parses it on every reconnect, so a daemon restart on a new port just works). This window is
 * SERVED BY the daemon — the page's own origin is the daemon's origin, and the token it was opened
 * with is in its own URL. There is nothing to discover and nothing to keep in step.
 */

export type ArtifactKind = 'image' | 'video' | 'audio' | 'file'

export interface Artifact {
  path: string
  name: string
  mime: string
  kind: ArtifactKind
  size?: number
}

/** The token this window was opened with. Same source as AGENT_ID — see client.ts. */
const TOKEN = new URL(location.href).searchParams.get('token') || ''

/** Absolute URL to stream one artifact's bytes from the daemon's guarded /file endpoint. */
export function fileUrl(path: string): string {
  const q = new URLSearchParams({ path })
  if (TOKEN) q.set('token', TOKEN)
  return `${location.origin}/file?${q.toString()}`
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

/**
 * Artifacts = media/document files an agent produced, detected server-side and carried
 * on chat events / history messages. The renderer fetches their bytes straight from the
 * daemon's HTTP /file endpoint (same host/port/token as the WebSocket).
 */

export type ArtifactKind = 'image' | 'video' | 'audio' | 'file'

export interface Artifact {
  path: string
  name: string
  mime: string
  kind: ArtifactKind
  size?: number
  /** In-memory content for a SYNTHETIC doc that has no file on disk — e.g. a tool/plugin
   *  description opened in the Canvas. When set, viewers render this text directly instead of
   *  reading `path`, and the doc is read-only (no Edit/Save, no open-in-app/download). `path`
   *  is then just a stable unique key for the viewer, not a real filesystem path. */
  text?: string
}

/** A self-declared canvas ACTION on an artifact (e.g. figure_to_svg's "Convert to Vector" on PNGs).
 *  Advertised by a backend tool via `artifact_action`, fetched from the plugin catalog. The button
 *  exists exactly when the tool is installed and the artifact's mime matches — no UI hardcoding. */
export interface ArtifactAction {
  tool: string // backend tool name to invoke (via tools.invoke)
  mime: string[] // artifact mimetypes it applies to
  label: string // button text
  param: string // the tool arg that receives the artifact's path
}

/** The actions that apply to one artifact (mime match). */
export function actionsFor(actions: ArtifactAction[], a: Artifact): ArtifactAction[] {
  return actions.filter((ac) => ac.mime.includes(a.mime))
}

// The daemon's HTTP origin + auth token, refreshed on every (re)connect from the same
// ws URL the gateway client resolves — so a daemon restart (new port/token) just works.
let httpOrigin = ''
let authToken = ''

/** Feed the resolved ws URL (ws://host:port/?token=…) so file URLs target the live daemon. */
export function setGatewayUrl(wsUrl: string): void {
  try {
    const u = new URL(wsUrl)
    httpOrigin = `${u.protocol === 'wss:' ? 'https:' : 'http:'}//${u.host}`
    authToken = u.searchParams.get('token') || ''
  } catch {
    /* keep the previous values if the URL is malformed */
  }
}

/** Absolute HTTP URL to stream one artifact's bytes from the daemon. */
export function fileUrl(path: string): string {
  const q = new URLSearchParams({ path })
  if (authToken) q.set('token', authToken)
  return `${httpOrigin}/file?${q.toString()}`
}

export function humanSize(bytes?: number): string {
  if (!bytes || bytes < 0) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let n = bytes
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i++
  }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`
}

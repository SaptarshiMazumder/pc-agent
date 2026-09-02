/**
 * Artifacts = media/document files an agent produced, detected server-side and carried
 * on chat events / history messages. The renderer fetches their bytes straight from the
 * daemon's HTTP /file endpoint (same host/port/token as the WebSocket).
 */

import { getSession } from './auth'
import { getMode } from './mode'

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

/** Absolute, authed URL to download an agent's built installer (daemon-served
 *  /product/<id>/installer, returned by the agents.installer RPC). Carries the ACCOUNT session so
 *  an ORG agent's ownership check resolves the caller's org membership — the machine token rides
 *  as the desktop fallback, exactly like appLaunchUrl. */
export function installerUrl(path: string): string {
  const q = new URLSearchParams()
  if (authToken) q.set('token', authToken)
  const session = getSession()?.token
  if (session) q.set('session', session)
  const query = q.toString()
  return `${httpOrigin}${path}${query ? (path.includes('?') ? '&' : '?') + query : ''}`
}

/** Absolute, tokenized launch URL for an AGENT APP's UI (daemon-served /apps/<id>/) —
 *  same live origin+token as /file; the scope pins the page's connection to its agent.
 *
 *  IDENTITY AND MODE TRAVEL WITH THE LAUNCH. The app window opens its own socket, and the
 *  daemon decides who a connection is from that socket alone — so without these two params a
 *  signed-in shell opened app windows that ran ANONYMOUS: no cloud billing, model calls
 *  falling to whatever dead BYOK keys the machine had. The SDK reads them off the page url
 *  (its own stored session, from signing in inside the app, still wins). */
export function appLaunchUrl(app: { url: string }, agentId: string): string {
  const q = new URLSearchParams({ scope: `agent:${agentId}` })
  if (authToken) q.set('token', authToken)
  const session = getSession()?.token
  const mode = getMode()
  if (session) q.set('session', session)
  if (mode) q.set('mode', mode)
  return `${httpOrigin}${app.url}?${q.toString()}`
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

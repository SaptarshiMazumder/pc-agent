/**
 * Agent presentation helpers — colour + initials, derived on the client.
 *
 * COPIED VERBATIM from the agentd renderer (clients/ui/src/lib/agentPresentation.ts) so this
 * window draws an agent's avatar EXACTLY as the desktop app does. The hue hash and the HSL
 * parameters also match the server's presentation.py, so an avatar rendered here before the
 * roster lands does not flip colour when the real value arrives. Divergence here would mean
 * the same agent wearing two different colours in two windows of one product.
 *
 * Nothing agent-specific is hardcoded here. The avatar/dot COLOUR is owned by the
 * server (unique across agents, persisted) and arrives on `AgentInfo.color`; this
 * module only provides a deterministic FALLBACK for the brief moment before that
 * lands (or for non-agent seeds like a project id). The fallback shares the server's
 * hue-from-id hash + HSL params, so it renders the SAME colour the server will pick
 * for any agent whose hue doesn't collide — no flip when the real value arrives.
 * Initials are a pure function of the name/id.
 */

const COLOR_SAT = 0.62 // matches presentation.py COLOR_SAT
const COLOR_LIGHT = 0.58 // matches presentation.py COLOR_LIGHT

function hueFromSeed(seed: string): number {
  const s = seed || '' // never throw on an undefined/empty seed (e.g. a row missing agentId)
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h % 360
}

/** HSL → #rrggbb, identical algorithm to Python's colorsys.hls_to_rgb. */
function hslToHex(hue: number, sat = COLOR_SAT, light = COLOR_LIGHT): string {
  const h = ((hue % 360) + 360) % 360 / 360
  const q = light < 0.5 ? light * (1 + sat) : light + sat - light * sat
  const p = 2 * light - q
  const channel = (t: number): number => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  const to2 = (x: number): string => Math.round(x * 255).toString(16).padStart(2, '0')
  return `#${to2(channel(h + 1 / 3))}${to2(channel(h))}${to2(channel(h - 1 / 3))}`
}

/** A stable colour hashed from any seed string — the fallback / non-agent colour. */
export function hashColor(seed: string): string {
  return hslToHex(hueFromSeed(seed))
}

// main wears the brand lime (matches presentation.MAIN_COLOR); reserved for it.
const MAIN_COLOR = '#a3e635'

/** The INTERNAL id of the default/generalist agent. It's routing plumbing, not a name —
 *  never render it; go through agentLabel() so users see the server-owned display name. */
export const MAIN_AGENT_ID = 'main'

/** An agent's avatar/dot colour: the server-assigned one, else the deterministic
 *  fallback so an avatar is never blank (main falls back to the brand lime). Tolerates an
 *  undefined/empty id (a session row from an older daemon may not carry agentId yet). */
export function agentColor(serverColor: string | undefined, id: string | undefined): string {
  return serverColor || ((id || '') === MAIN_AGENT_ID ? MAIN_COLOR : hashColor(id || ''))
}

/** The user-facing label for an agent. The server-owned name wins; the internal id
 *  'main' is NEVER shown — for the default agent a missing/id-shaped name (older daemon,
 *  roster not loaded yet) degrades to the hello's assistant name, then a neutral word.
 *  Named agents keep their id as the fallback (those ids are user-authored and meaningful). */
export function agentLabel(
  name: string | undefined,
  id: string | undefined,
  assistantName?: string
): string {
  const aid = id || ''
  const n = (name || '').trim()
  if (aid === MAIN_AGENT_ID) {
    return (n && n !== MAIN_AGENT_ID ? n : '') || assistantName || 'Assistant'
  }
  return n || aid || 'agent'
}

/** Fallback tagline only — the real one is server-owned (`AgentInfo.tagline`). */
export function agentTag(_id: string): string {
  return 'agent'
}

/** Initials from the name (or id) — dynamic, no lookup table. */
export function agentInitials(name: string | undefined, id: string | undefined): string {
  const s = (name || id || '?').trim()
  const parts = s.split(/[\s-]+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return s.slice(0, 2).toUpperCase()
}

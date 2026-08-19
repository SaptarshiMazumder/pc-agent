/**
 * Reading an access token's own claims, WITHOUT verifying them.
 *
 * Nothing is authorised on the strength of what comes out of here. The daemon checks the
 * signature and would reject a token whose claims we misread in our own favour. These readers
 * only decide what the PAGE should do with a credential it already holds: when to stop pretending
 * it works, and whether it belongs to this window at all.
 */

/** A credential this platform can still USE.
 *
 * Tokens are signed JWTs (three dot-separated parts). The opaque `sess_...` sessions that came
 * before them cannot be resolved by any current daemon, so a stored one is not a session — it is a
 * guarantee of failure. Keeping one looked harmless and was not: the page reported itself signed
 * in, presented the dead token on every connect, and the daemon refused each one — an endless
 * reconnect against our own server that no amount of retrying could fix.
 */
export function usable(token: string): boolean {
  return !!token && !token.startsWith('sess_') && token.split('.').length === 3
}

function claims(token: string): Record<string, unknown> | null {
  try {
    const body = (token || '').split('.')[1]
    if (!body) return null
    // base64url -> base64. atob is the one decoder present in every browser and in Node 16+.
    return JSON.parse(atob(body.replace(/-/g, '+').replace(/_/g, '/'))) as Record<string, unknown>
  } catch {
    return null // not our token shape — `usable` already refuses those
  }
}

/** When an access token dies, in epoch ms, from its own `exp`. 0 when unreadable. */
export function accessTokenExpiry(token: string): number {
  const exp = Number(claims(token)?.exp || 0)
  return exp > 0 ? exp * 1000 : 0
}

/** Which account an access token speaks for, from its `sub`. '' when unreadable. */
export function accessTokenAccount(token: string): string {
  return String(claims(token)?.sub || '')
}

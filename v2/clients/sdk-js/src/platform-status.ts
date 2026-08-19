/**
 * What the DAEMON says about the platform it is part of: where people sign in, and whether a
 * model proxy exists to switch to.
 *
 * Its own module because two things need it and they must not import each other: the sign-in flow
 * (auth.ts) and the token manager that renews what sign-in produced (identity.ts). A leaf.
 */

/** Options shared by everything that talks to the daemon over plain HTTP. */
export interface DaemonOptions {
  /** Daemon HTTP origin. Defaults to the page's own — an agent app is served BY the daemon. */
  origin?: string
  /** The daemon's bearer token. Defaults to `?token=` on the page URL. */
  token?: string
  timeoutMs?: number
}

// 15s was not enough for SIGNUP. The accounts service hashes at 200k PBKDF2 rounds and then writes
// to a network filesystem, which on a small container measures in the tens of seconds — so a
// correct signup was being reported to the user as "login timed out". Sign-in itself is
// sub-second; this ceiling exists for the slow path, and the honest fix for THAT is on the server,
// not a bigger number here.
export const DEFAULT_TIMEOUT = 45000

export function daemonOrigin(opts: DaemonOptions): string {
  if (opts.origin) return opts.origin.replace(/\/$/, '')
  if (typeof location === 'undefined') throw new Error('no origin: pass options.origin')
  return location.origin
}

export function daemonToken(opts: DaemonOptions): string {
  if (typeof opts.token === 'string') return opts.token
  if (typeof location === 'undefined') return ''
  try {
    return new URL(location.href).searchParams.get('token') || ''
  } catch {
    return ''
  }
}

export async function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>
  const guard = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms}ms`)), ms)
  })
  try {
    return await Promise.race([p, guard])
  } finally {
    clearTimeout(timer!)
  }
}

/** The daemon's own view: where sign-in lives, and whether a proxy exists to switch to. */
export async function platformStatus(opts: DaemonOptions): Promise<Record<string, any>> {
  const u = new URL('/platform/status', `${daemonOrigin(opts)}/`)
  const token = daemonToken(opts)
  if (token) u.searchParams.set('token', token)
  const r = await withTimeout(
    fetch(u.toString(), { cache: 'no-store' }),
    opts.timeoutMs ?? DEFAULT_TIMEOUT,
    'platform status'
  )
  if (!r.ok) throw new Error(`platform status failed (HTTP ${r.status})`)
  return (await r.json()) as Record<string, any>
}

/** Just the accounts service address, or '' when this daemon has none. */
export async function accountsUrl(opts: DaemonOptions): Promise<string> {
  const status = await platformStatus(opts)
  return String(status.accountsUrl || '').replace(/\/$/, '')
}

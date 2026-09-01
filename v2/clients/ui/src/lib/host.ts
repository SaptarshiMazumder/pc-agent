/**
 * Facts about the HOST this renderer is running in. A leaf: it imports nothing.
 *
 * These two lived in lib/platform.ts, which imports lib/auth.ts (it reads the session to build a
 * daemon URL). auth.ts imported `randomUuid` back out of platform.ts, so the two already formed a
 * cycle — survivable, because ES module bindings are live and neither value is read at module
 * scope, but a trap for whoever next moves an initialiser to the top level. The failure would be
 * an `undefined` on one import order only.
 *
 * Signing in made it worse rather than better: auth.ts now needs `isDesktop` too, because sign-in
 * goes through the local daemon on desktop and cannot on the web (there the session token IS the
 * socket credential, so there is no connection to sign in through yet).
 *
 * They all moved here, and platform.ts re-exports them, so no other importer changes.
 */

/** The desktop preload bridge (src/preload/index.ts), absent in a plain browser.
 *
 * IDENTIFIED BY WHAT IT CAN DO, not by its name. Two different things publish themselves as
 * `window.agentd`: this preload (contextBridge.exposeInMainWorld("agentd", api)) and the
 * agent-app SDK (clients/sdk-js builds an IIFE with globalName: 'agentd'). Testing for the name
 * alone therefore reported `isDesktop === true` on every page served at /apps/<id>/ — so shared
 * components took the Electron path inside a browser window: file reads went to a bridge that
 * was really the SDK, and the URL they fell back to carried no credential, which the daemon
 * answered with 401. The filesystem methods are the bridge's distinguishing feature and no SDK
 * will ever have them.
 */
const candidate = (globalThis as { agentd?: { readText?: unknown } }).agentd
const bridge = typeof candidate?.readText === 'function' ? candidate : undefined

export const isDesktop = !!bridge

/** The machine's sign-in, asked through the desktop shell's main process — the renderer page
 *  is file://, cross-origin to the daemon, and the daemon's HTTP surface answers no CORS, so a
 *  page fetch can never read /auth/*. Null in a browser, where the web sign-in path serves.
 *  Here rather than in platform.ts because the caller is lib/auth.ts, and going through the
 *  adapter would close the auth -> platform -> auth cycle this leaf exists to break. */
export interface HostAuthAnswer {
  status: number
  body: Record<string, unknown>
}

export function hostAuthRequest(
  path: string,
  headers?: Record<string, string>
): Promise<HostAuthAnswer> | null {
  const b = bridge as
    | { authRequest?: (p: string, h?: Record<string, string>) => Promise<HostAuthAnswer> }
    | undefined
  return typeof b?.authRequest === 'function' ? b.authRequest(path, headers) : null
}

/** OS-encrypted storage for the refresh token, when the desktop shell provides it.
 *
 * Reached from HERE rather than through lib/platform.ts on purpose. lib/tokens.ts needs it, and
 * platform.ts imports lib/auth.ts, which imports tokens.ts — so going through the adapter would
 * close exactly the cycle this leaf module exists to break, on the one code path that runs before
 * anything else during boot. Returns undefined on the web, where localStorage is the fallback.
 */
export function hostSecrets():
  | { read(): Promise<string | null>; write(token: string | null): Promise<void> }
  | undefined {
  const b = bridge as
    | { secrets?: { read(): Promise<string | null>; write(t: string | null): Promise<void> } }
    | undefined
  return b?.secrets && typeof b.secrets.read === 'function' ? b.secrets : undefined
}

/**
 * The viewer's OS, using the same tags the registry stamps on an installer
 * ('win' | 'mac' | 'linux'; '' when it cannot be told).
 *
 * Here rather than in platform.ts for the same reason as the two above: it is a host FACT that
 * reads nothing but `navigator`, and the public marketplace page needs it to pick which installer
 * to offer while having no daemon, no session and no reason to pull in the whole platform
 * adapter. platform.ts re-exports it, so no existing importer changes.
 *
 * Offering the wrong installer hands someone a file their machine cannot run, which is worse than
 * offering none — so this stays conservative and returns '' when it cannot tell.
 *
 * ORDER MATTERS: "darwin" contains the substring "win", so a naive /win/ test first would send
 * every Mac user a .exe. macOS is therefore matched before Windows.
 */
export function hostOs(): string {
  const nav = (globalThis as { navigator?: { userAgent?: string; platform?: string } }).navigator
  const s = `${nav?.platform || ''} ${nav?.userAgent || ''}`.toLowerCase()
  if (/mac|darwin|iphone|ipad/.test(s)) return 'mac'
  if (/win/.test(s)) return 'win'
  if (/linux|android|x11/.test(s)) return 'linux'
  return ''
}

/**
 * A v4 UUID, on every origin we actually ship to.
 *
 * `crypto.randomUUID` exists ONLY in a secure context — HTTPS or localhost. The desktop app and
 * `npm run dev` both qualify, so it is present everywhere we develop and absent on the one place
 * users hit first: the HTTP-only dev ALB. There it is not a degraded API, it is `undefined`, so
 * calling it throws `crypto.randomUUID is not a function` and takes the whole send path down.
 * That is the failure this exists to prevent, and no amount of local testing would have found it.
 *
 * `crypto.getRandomValues` is NOT secure-context-gated, so the fallback is still real randomness
 * — it hand-assembles the same RFC 4122 v4 layout (version nibble in byte 6, variant bits in byte
 * 8) that randomUUID would have produced. The shape matters beyond tidiness: `monitoring/trace.ps1`
 * tells a browser-minted id from the daemon's fallback by whether it is DASHED, so an id of a
 * different shape would silently break that diagnostic.
 *
 * The Math.random branch is for a browser with no Web Crypto at all. Weak, and deliberately last —
 * these ids are correlation keys, never secrets or tokens.
 */
export function randomUuid(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()

  const bytes = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') {
    c.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10x
  const hex: string[] = []
  for (let i = 0; i < 16; i++) hex.push(bytes[i].toString(16).padStart(2, '0'))
  return (
    hex.slice(0, 4).join('') +
    '-' +
    hex.slice(4, 6).join('') +
    '-' +
    hex.slice(6, 8).join('') +
    '-' +
    hex.slice(8, 10).join('') +
    '-' +
    hex.slice(10, 16).join('')
  )
}

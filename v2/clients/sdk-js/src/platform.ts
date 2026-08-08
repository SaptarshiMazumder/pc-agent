/**
 * Hosted-platform sign-in — the MECHANISM half (no DOM; see gate.ts for the UI).
 *
 * WHY THIS IS IN THE SDK
 * An agent app that runs on platform keys has to sign the user in, and until now exactly one
 * agent did it: figure-creator, in 150 hand-written lines inside its own ui/app.js. Every other
 * agent app therefore could not use cloud mode at all — not because anything was missing from
 * the daemon, but because the agent had no login. Copying those lines into each new agent would
 * mean N copies of a flow whose failure mode is "signs in, then silently has no model access".
 * One implementation here, and every agent that vendors the SDK gets it.
 *
 * THE FLOW, and why it has the shape it does:
 *
 *   1. GET /platform/status on the LOCAL daemon      -> accountsUrl + whether keys are already live
 *   2. POST <accountsUrl>/signup  (only when creating)
 *   3. POST <accountsUrl>/login                      -> a session token
 *   4. GET /platform/connect?session=<token>         -> the daemon persists it as its model-proxy
 *                                                        credential and re-configures LIVE
 *   5. poll /platform/status if step 4's reply is lost
 *
 * Step 5 is not defensive padding. Step 4 makes the daemon rewrite its own credential and
 * reconfigure the proxy mid-request; the reply can be lost while the work SUCCEEDS. Without the
 * poll the user sees a failure and signs in again, against an install that is already connected.
 *
 * TWO DEPLOYMENTS, one flow. On a desktop install the daemon is local and step 4 binds it. On a
 * hosted daemon the model key is server-owned, so /platform/connect refuses by design
 * ("this deployment manages platform keys server-side"). That refusal is SUCCESS, not failure:
 * the session token is instead the credential for the socket. Callers get the token back either
 * way, so neither case needs a different code path in the app.
 *
 * Nothing here is hardcoded to a product or an environment: the accounts endpoint comes from the
 * daemon's own distribution profile, and an install with no accountsUrl is a BYOK build where no
 * sign-in should ever be shown.
 */

/** What /platform/status says, reduced to the three things a caller acts on. */
export interface PlatformStatus {
  /** Where sign-in requests go. '' => a BYOK build: there is no account system, show no gate. */
  accountsUrl: string
  /** Shorthand for accountsUrl !== ''. */
  hosted: boolean
  /** The daemon already holds a working platform credential — the user is effectively signed in. */
  keysLive: boolean
  /** The untouched payload, for callers that want diagnostics/modelProxy details. */
  raw: Record<string, any>
}

export interface SignInResult extends PlatformStatus {
  /** The accounts session token. Also the socket credential on a hosted daemon. */
  token: string
}

export interface PlatformOptions {
  /** Daemon HTTP origin. Defaults to the page's own origin, which is correct for an app served
   *  at /apps/<id>/ — the daemon serves the page, so same-origin needs no CORS and no config. */
  origin?: string
  /** Per-request timeout. */
  timeoutMs?: number
  /** How many times to re-check status when a connect reply is lost (see module docstring). */
  confirmAttempts?: number
  /** Delay between those checks. */
  confirmDelayMs?: number
  /** Storage key for the session. Defaults to a key derived from the agent id in the page URL,
   *  so two agent apps on one machine never share or clobber each other's session. */
  storageKey?: string
  /** The DAEMON's bearer token. Defaults to `?token=` on the page URL — the same credential
   *  `fromPage()` uses for the socket.
   *
   *  NOT optional in practice: /platform/status and /platform/connect authorize exactly like
   *  /file ("the daemon's bearer token must match when one is set"), so omitting it makes every
   *  call 401 on any daemon with auth enabled — which is all of them. The failure is silent in
   *  the worst way: the gate cannot read status, gives up, and the app runs on with no model
   *  access. Distinct from the ACCOUNTS session token, which rides in `?session=`. */
  token?: string
}

const DEFAULTS = {
  timeoutMs: 15000,
  confirmAttempts: 6,
  confirmDelayMs: 1000
}

/** The refusal a HOSTED daemon returns for /platform/connect. Matched loosely (substring, lower
 *  case) so a reworded message degrades to "retry the poll" rather than a hard failure. */
const SERVER_SIDE_KEYS = 'server-side'

function origin(opts: PlatformOptions = {}): string {
  if (opts.origin) return opts.origin.replace(/\/$/, '')
  if (typeof location === 'undefined') throw new Error('no origin: pass options.origin')
  return location.origin
}

/** The agent this page belongs to: ?scope=agent:<id>, else the /apps/<id>/ path segment.
 *  Derived, never configured — the opener already states it both ways. */
export function agentIdFromPage(): string {
  if (typeof location === 'undefined') return ''
  const here = new URL(location.href)
  const scope = here.searchParams.get('scope') || ''
  const scoped = /^agent:(.+)$/.exec(scope)
  if (scoped) return scoped[1]
  const path = /\/apps\/([^/]+)/.exec(here.pathname)
  return path ? decodeURIComponent(path[1]) : ''
}

/** The daemon bearer token: explicit option, else `?token=` from the page the opener built. */
function daemonToken(opts: PlatformOptions = {}): string {
  if (typeof opts.token === 'string') return opts.token
  if (typeof location === 'undefined') return ''
  try {
    return new URL(location.href).searchParams.get('token') || ''
  } catch {
    return ''
  }
}

/** A daemon URL with the bearer token attached (and any extra query params). */
function daemonUrl(path: string, params: Record<string, string>, opts: PlatformOptions = {}): string {
  const u = new URL(path, `${origin(opts)}/`)
  const token = daemonToken(opts)
  if (token) u.searchParams.set('token', token)
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v)
  return u.toString()
}

function storageKey(opts: PlatformOptions = {}): string {
  if (opts.storageKey) return opts.storageKey
  const id = agentIdFromPage()
  return `agentd.session.${id || 'app'}`
}

async function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: any
  const guard = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms}ms`)), ms)
  })
  try {
    return await Promise.race([p, guard])
  } finally {
    clearTimeout(timer)
  }
}

async function getJson(url: string, timeoutMs: number, what: string): Promise<any> {
  const r = await withTimeout(fetch(url, { cache: 'no-store' }), timeoutMs, what)
  const text = await r.text()
  let body: any = {}
  try {
    body = text ? JSON.parse(text) : {}
  } catch {
    /* a non-JSON body is reported through the status check below */
  }
  if (!r.ok) throw new Error(String(body?.error || `HTTP ${r.status}`))
  return body
}

async function postJson(url: string, payload: any, timeoutMs: number, what: string): Promise<any> {
  const r = await withTimeout(
    fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    }),
    timeoutMs,
    what
  )
  const text = await r.text()
  let body: any = {}
  try {
    body = text ? JSON.parse(text) : {}
  } catch {
    /* fall through to the status check */
  }
  if (!r.ok) throw new Error(String(body?.error || body?.detail || `HTTP ${r.status}`))
  return body
}

/** modelProxy, with the pre-rename alias still honoured — an older daemon only sends
 *  modelGateway, and reading one name would make this look "not signed in" forever there. */
function proxyOf(raw: any): any {
  return (raw && (raw.modelProxy || raw.modelGateway)) || null
}

function shape(raw: any): PlatformStatus {
  const accountsUrl = String(raw?.accountsUrl || '').replace(/\/$/, '')
  return {
    accountsUrl,
    hosted: !!accountsUrl,
    keysLive: !!proxyOf(raw)?.enabled,
    raw: raw || {}
  }
}

/** The daemon's hosted-platform view. Throws only when the daemon is unreachable — a reachable
 *  daemon on a BYOK build answers normally with an empty accountsUrl. */
export async function platformStatus(opts: PlatformOptions = {}): Promise<PlatformStatus> {
  const timeoutMs = opts.timeoutMs ?? DEFAULTS.timeoutMs
  return shape(await getJson(daemonUrl('/platform/status', {}, opts), timeoutMs, 'platform status'))
}

/**
 * Hand a session token to the daemon so it runs on platform keys.
 *
 * Resolves with keysLive true once the credential is live. Three ways that happens, all normal:
 * the connect reply says so; the reply was lost and a follow-up status check finds it live; or the
 * daemon manages keys server-side and refuses, in which case the token is the socket credential
 * and there is nothing local to bind.
 */
export async function platformConnect(
  session: string,
  opts: PlatformOptions = {}
): Promise<PlatformStatus> {
  const timeoutMs = opts.timeoutMs ?? DEFAULTS.timeoutMs
  const attempts = opts.confirmAttempts ?? DEFAULTS.confirmAttempts
  const delay = opts.confirmDelayMs ?? DEFAULTS.confirmDelayMs
  // `session` is the ACCOUNTS token; daemonUrl adds the DAEMON's bearer token. Two different
  // credentials on one request — conflating them is how this endpoint gets called unauthorized.
  const url = daemonUrl('/platform/connect', { session }, opts)

  try {
    const status = shape(await getJson(url, timeoutMs, 'platform connect'))
    if (status.keysLive) return status
  } catch (e) {
    const message = String((e as Error)?.message || e).toLowerCase()
    if (message.includes(SERVER_SIDE_KEYS)) {
      // A hosted daemon: keys are server-owned, the token is the socket credential. Report the
      // real status but treat it as connected — there is no local credential to wait for.
      const status = await platformStatus(opts)
      return { ...status, keysLive: true }
    }
    // Otherwise fall through to the poll: the work may have succeeded with the reply lost.
  }

  for (let i = 0; i < attempts; i++) {
    await new Promise((r) => setTimeout(r, delay))
    try {
      const status = await platformStatus(opts)
      if (status.keysLive) return status
    } catch {
      /* keep trying — the daemon is mid-reconfigure */
    }
  }
  throw new Error('signed in, but this device did not activate — try again')
}

// ------------------------------------------------------------------ session persistence

export function loadSession(opts: PlatformOptions = {}): { token: string; email?: string } | null {
  try {
    const raw = localStorage.getItem(storageKey(opts))
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && parsed.token ? parsed : null
  } catch {
    return null // private mode / storage disabled — sign-in still works, it just won't persist
  }
}

export function saveSession(
  value: { token: string; email?: string } | null,
  opts: PlatformOptions = {}
): void {
  try {
    if (value) localStorage.setItem(storageKey(opts), JSON.stringify(value))
    else localStorage.removeItem(storageKey(opts))
  } catch {
    /* non-fatal */
  }
}

// ------------------------------------------------------------------ the two actions

/** Sign in (or sign up), bind this install, and remember the session. */
export async function signIn(
  args: { email: string; password: string; signup?: boolean },
  opts: PlatformOptions = {}
): Promise<SignInResult> {
  const timeoutMs = opts.timeoutMs ?? DEFAULTS.timeoutMs
  const status = await platformStatus(opts)
  if (!status.hosted) throw new Error('this build has no sign-in server configured')

  const email = args.email.trim().toLowerCase()
  if (args.signup) {
    await postJson(`${status.accountsUrl}/signup`, { email, password: args.password }, timeoutMs, 'signup')
  }
  const login = await postJson(
    `${status.accountsUrl}/login`,
    { email, password: args.password },
    timeoutMs,
    'login'
  )
  const token = String(login?.token || login?.session || '')
  if (!token) throw new Error('the accounts server returned no session token')

  const connected = await platformConnect(token, opts)
  saveSession({ token, email }, opts)
  return { ...connected, token }
}

/** Forget the stored session. The daemon keeps its own credential until it is replaced —
 *  clearing that is a separate, privileged action, not something an app page should do. */
export function signOut(opts: PlatformOptions = {}): void {
  saveSession(null, opts)
}

/**
 * Resolve the sign-in state WITHOUT any UI: is a gate needed, and if not, why not.
 * gate.ts uses this to decide whether to render at all; an app with its own UI can use it
 * directly.
 */
export async function resolveAuth(
  opts: PlatformOptions = {}
): Promise<{ needsSignIn: boolean; status: PlatformStatus; token: string }> {
  const status = await platformStatus(opts)
  if (!status.hosted || status.keysLive) {
    return { needsSignIn: false, status, token: loadSession(opts)?.token || '' }
  }
  // Hosted but not connected: a stored token usually just works (a fresh daemon, same account).
  const stored = loadSession(opts)
  if (stored?.token) {
    try {
      const reconnected = await platformConnect(stored.token, opts)
      if (reconnected.keysLive) {
        return { needsSignIn: false, status: reconnected, token: stored.token }
      }
    } catch {
      /* stale or rejected — drop it and ask */
    }
    saveSession(null, opts)
  }
  return { needsSignIn: true, status, token: '' }
}

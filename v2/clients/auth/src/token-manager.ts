/**
 * TokenManager — the ONE place that mints, keeps and renews a credential.
 *
 * There were two of these and they disagreed. This is the one that was right, generalised so the
 * agent SDK can use it too, with the three defects the other copy had fixed rather than carried:
 *
 *  1. RENEW A TOKEN THAT HAS ALREADY EXPIRED. The old SDK guarded renewal with
 *     `life > 0 && life < 10min`, so the moment a token actually died — a sleeping laptop, a
 *     throttled background tab, a long agent run — renewal declined to act, and never acted
 *     again. Expiry is the reason to refresh, not a reason to stop.
 *
 *  2. SINGLE-FLIGHT. Refresh tokens are single-use and rotating, and the server treats a second
 *     use as theft: it revokes the whole family, which signs the user out EVERYWHERE. Two windows
 *     waking together, or one firing two ticks, was enough to trigger it. Every caller here shares
 *     one promise.
 *
 *  3. A REFUSED REFRESH IS TERMINAL; A FAILED ONE IS NOT. 401/403 means the family is gone — clear
 *     it and let the host show a form. Anything else is a network or a bad afternoon, and must NOT
 *     sign anyone out.
 *
 * WHAT IT DELIBERATELY DOES NOT KNOW: what a socket is, where the accounts service lives, or how
 * this host keeps a secret. All three arrive through `AuthConfig` — which is what lets one
 * implementation serve a desktop app with an OS keychain and an agent window with localStorage.
 */

import { accessTokenAccount, accessTokenExpiry, usable } from './claims'
import type { AuthConfig, LoginResponse, TokenPair } from './types'

/** Treat a token as spent slightly BEFORE the cliff, so renewal beats the first failed request. */
const EXPIRY_SKEW_MS = 30_000

/** Never busy-loop, and never schedule in the past. */
const MIN_DELAY_MS = 5_000

/** No `expiresAt` to work from (a server that sent neither `expires_in` nor a readable `exp`). */
const BLIND_POLL_MS = 300_000

const DEFAULT_TIMEOUT_MS = 45_000

export class TokenManager {
  private pair: TokenPair | null = null
  private inflight: Promise<TokenPair | null> | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private readonly listeners = new Set<(p: TokenPair | null) => void>()
  private wake: (() => void) | null = null

  constructor(private readonly config: AuthConfig) {
    this.pair = this.readStored()
  }

  // ------------------------------------------------------------------- reading

  /** What is held right now, WITHOUT renewing. Synchronous, for a socket URL or a rendered email. */
  current(): TokenPair | null {
    return this.pair
  }

  /** Is there a credential this client can still use, or still renew? */
  signedIn(): boolean {
    const p = this.pair
    if (!p || !usable(p.accessToken)) return false
    // An expired access token with a refresh token behind it is NOT signed out: renewal is one
    // HTTP call away, and reporting it as signed out puts a login form in front of somebody who
    // never left.
    if (p.refreshToken || !this.expired(p)) return true
    // Spent AND unrenewable — the state a window opened by the desktop app reaches when nothing
    // has fed it for ten minutes. EVICT rather than merely answer false: leaving it in storage
    // means every other path that looks there finds it again, and the daemon does not refuse a
    // dead token — it accepts the reconnect ANONYMOUSLY, so the account's agents disappear from
    // the window with no error and no sign-in form. Dropping it produces one visible prompt.
    this.replace(null)
    return false
  }

  /**
   * A USABLE access token, renewing first when the one we hold is spent.
   *
   * The only way anything should ever obtain a credential, so that no caller anywhere has to
   * reason about expiry — which is exactly the reasoning every caller previously got wrong.
   */
  async accessToken(): Promise<string> {
    const p = this.pair
    if (p && !this.expired(p)) return p.accessToken
    const next = await this.refresh()
    return next?.accessToken || ''
  }

  subscribe(cb: (p: TokenPair | null) => void): () => void {
    this.listeners.add(cb)
    return () => this.listeners.delete(cb)
  }

  // ------------------------------------------------------------------- writing

  /**
   * Sign in, creating the account first when `signup`.
   *
   * THROWS on a rejected credential, carrying the service's own message ("incorrect password") so
   * a form has something to show. A failed attempt must never resolve to a signed-out state: the
   * caller cannot tell that apart from having signed out, and the user is left looking at a form
   * that cleared itself.
   */
  async login(args: { email: string; password: string; signup?: boolean }): Promise<TokenPair> {
    const base = await this.base()
    const email = args.email.trim().toLowerCase()
    if (args.signup) {
      await this.post(`${base}/signup`, { email, password: args.password }, 'signup')
    }
    // `/auth/login`, never `/login`. The latter is a compatibility alias kept for already-published
    // scripts (accounts/app.py says so in as many words), and pointing half the clients at it is
    // how agent windows ended up missing from the user's own device list.
    const data = (await this.post(
      `${base}/auth/login`,
      {
        email,
        password: args.password,
        client_id: this.config.clientId,
        device_label: this.deviceLabel()
      },
      'login'
    )) as LoginResponse
    const next = this.toPair(data, email)
    if (!next.accessToken) throw new Error('the accounts server returned no access token')
    await this.set(next)
    return next
  }

  /**
   * Re-establish a session at start-up.
   *
   * This is what makes "stay signed in" work with a ten-minute access token: nothing durable is
   * kept but the refresh token, and one exchange at boot turns it into a usable pair. A window
   * holding no refresh token (opened by the desktop app, and fed rather than renewing) keeps
   * whatever it was handed — unless that has died, in which case it is dropped, because a page
   * presenting a dead token is not refused, it is accepted ANONYMOUSLY.
   */
  async restore(): Promise<TokenPair | null> {
    const stored = this.pair || this.readStored()
    if (!stored?.refreshToken) {
      // FED, BUT ABLE TO STOP BEING FED. A window opened by the desktop app arrives holding an
      // access token and nothing else, so it cannot renew and goes anonymous ten minutes later —
      // which the daemon does not refuse, so the user simply watches their agents disappear.
      // While that token is still ALIVE it is proof of the account, and proof is all `/auth/derive`
      // wants: the window trades it for a chain of its OWN and looks after itself from then on.
      //
      // A NEW CHAIN, never a copy of the shell's. Refresh tokens are single-use and rotating, so
      // two holders of one is not sharing — the second to spend it looks like theft and the server
      // signs both out.
      if (stored && !this.expired(stored)) {
        const own = await this.derive()
        if (own) return own
      }
      if (stored && this.expired(stored)) await this.set(null)
      return this.pair
    }
    return this.refresh()
  }

  /**
   * Trade a live access token for a session of this client's own. Returns null when there is
   * nothing live to trade, or the server declined.
   *
   * NEVER THROWS. It runs on a boot path beside things that matter more; a window that cannot
   * derive is no worse off than it was a moment ago — it still holds a working access token, and
   * it degrades to exactly the old behaviour rather than failing to start.
   */
  async derive(): Promise<TokenPair | null> {
    const held = this.pair
    if (!held || this.expired(held)) return null
    try {
      const data = (await this.post(
        `${await this.base()}/auth/derive`,
        {
          access_token: held.accessToken,
          client_id: this.config.clientId,
          device_label: this.deviceLabel()
        },
        'derive'
      )) as LoginResponse
      const next = this.toPair(data, held.email)
      if (!next.refreshToken) return null // nothing gained; keep what we have
      await this.set(next)
      return next
    } catch {
      return null
    }
  }

  /**
   * Trade the refresh token for a new pair. SINGLE-FLIGHT — see the header.
   *
   * Returns null when the session is over, having cleared it; and null WITHOUT clearing when the
   * attempt merely failed. The difference is the whole point.
   */
  refresh(): Promise<TokenPair | null> {
    if (this.inflight) return this.inflight
    this.inflight = this.exchange().finally(() => {
      this.inflight = null
    })
    return this.inflight
  }

  private async exchange(): Promise<TokenPair | null> {
    const token = this.pair?.refreshToken || (await this.readSecret())
    if (!token) return null
    let base = ''
    try {
      base = await this.base()
    } catch {
      return null // discovery has not resolved yet; the next tick tries again
    }
    let res: Response
    try {
      res = await this.send(`${base}/auth/refresh`, {
        refresh_token: token,
        client_id: this.config.clientId
      })
    } catch {
      return null // offline. KEEP the credential: a flaky network is not a sign-out.
    }
    if (!res.ok) {
      // Terminal only when the server SAYS so. Expired, revoked, or the family killed for reuse;
      // retrying forever with a dead credential is how a page goes anonymous without saying so.
      if (res.status === 401 || res.status === 403) await this.set(null)
      return null
    }
    const data = (await res.json().catch(() => ({}))) as LoginResponse
    const next = this.toPair(data, this.pair?.email || '')
    if (!next.accessToken) return null
    // A server that rotates without returning a new refresh token leaves the old one valid. Keep
    // it rather than dropping to a session that can never renew again.
    if (!next.refreshToken) next.refreshToken = token
    if (!next.accountId && this.pair?.accountId) next.accountId = this.pair.accountId
    await this.set(next)
    return next
  }

  /**
   * Write a credential directly, with NO account check. The unguarded door.
   *
   * There is exactly one honest use: a host adopting a credential an opener handed it, such as the
   * `?session=` on an agent window's launch URL. Everything else — sign-in, renewal, a token
   * pushed by the desktop app — has a guarded path above, and using this instead skips the check
   * that path exists for.
   *
   * Synchronous in effect: the pair is live the moment this returns, because a caller that writes
   * a session and immediately builds a socket URL from it cannot wait for a keychain round trip.
   */
  replace(pair: TokenPair | null): void {
    void this.set(pair)
  }

  /**
   * Adopt an access token minted elsewhere — the desktop app pushing one into an agent window.
   *
   * WHOSE TOKEN IS THIS? The push reaches EVERY open window at once and cannot know that one of
   * them signed in as somebody else. Adopting it there would leave this account's email and
   * refresh token stored beside another account's access token, and land the window on the wrong
   * account while still displaying this one's address. An unreadable token fails CLOSED.
   *
   * Holding no accountId is the ordinary case, not an exception: a window opened BY the desktop
   * app took its credential from the launch URL and recorded no account, so it has nothing to
   * disagree with and accepts every push.
   */
  async adopt(accessToken: string): Promise<boolean> {
    if (!usable(accessToken)) return false
    const held = this.pair
    if (held?.accountId && accessTokenAccount(accessToken) !== held.accountId) return false
    await this.set({
      accessToken,
      refreshToken: held?.refreshToken || '',
      expiresAt: accessTokenExpiry(accessToken),
      accountId: held?.accountId || '',
      email: held?.email || ''
    })
    return true
  }

  /**
   * Forget this client's session, and tell the server so.
   *
   * A sign-out that only forgets locally leaves a 30-day credential alive on a machine the user
   * may have just decided they do not trust. Best-effort: being offline must not block signing out.
   */
  async logout(): Promise<void> {
    const token = this.pair?.refreshToken || (await this.readSecret())
    await this.set(null)
    if (!token) return
    try {
      const base = await this.base()
      await this.send(`${base}/auth/logout`, { refresh_token: token })
    } catch {
      /* offline — the token still expires on its own */
    }
  }

  // ------------------------------------------------------------------- renewal

  /**
   * Keep the credential fresh for as long as the host lives. Returns a stop function.
   *
   * TWO TRIGGERS, because a timer alone is provably not enough. Timers do not fire while a machine
   * sleeps and are throttled in background tabs, so a window that was away comes back holding a
   * token that died hours ago — the single most common way this used to break, and the one a
   * schedule can never cover. Coming back is therefore its own trigger.
   */
  start(): () => void {
    this.schedule()
    if (typeof document !== 'undefined' && !this.wake) {
      this.wake = () => {
        if (document.visibilityState === 'visible') void this.tick()
      }
      document.addEventListener('visibilitychange', this.wake)
      if (typeof addEventListener === 'function') addEventListener('focus', this.wake)
    }
    return () => this.stop()
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    if (!this.wake) return
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.wake)
    }
    if (typeof removeEventListener === 'function') removeEventListener('focus', this.wake)
    this.wake = null
  }

  private async tick(): Promise<void> {
    const p = this.pair
    // NO `life > 0` GUARD HERE. That was the bug: it made an already-dead token the one case
    // renewal refused to handle, which is the only case that actually matters.
    if (p?.refreshToken && this.expiringSoon(p)) await this.refresh()
    this.schedule()
  }

  private schedule(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    const p = this.pair
    if (!p?.refreshToken) return
    // Read from the CURRENT pair, AFTER any refresh. Computing this from the pre-refresh value
    // scheduled the next attempt against a lifetime that no longer existed.
    if (!p.expiresAt) {
      this.timer = setTimeout(() => void this.tick(), BLIND_POLL_MS)
      return
    }
    const life = p.expiresAt - Date.now()
    this.timer = setTimeout(() => void this.tick(), Math.max(MIN_DELAY_MS, Math.floor(life * 0.8)))
  }

  private expired(p: TokenPair): boolean {
    return p.expiresAt > 0 && Date.now() > p.expiresAt - EXPIRY_SKEW_MS
  }

  /** Close enough to the end to be worth renewing now — or already past it. */
  private expiringSoon(p: TokenPair): boolean {
    if (!p.expiresAt) return true
    return Date.now() > p.expiresAt - Math.max(EXPIRY_SKEW_MS, 120_000)
  }

  // ------------------------------------------------------------------- storage

  private async set(next: TokenPair | null): Promise<void> {
    this.pair = next
    // PERSISTENCE MUST NEVER BLOCK SIGN-IN. The desktop writes through IPC to the OS keychain, and
    // any failure there — bridge missing, keychain unavailable, handler rejected — used to
    // propagate out of login() and land in the form as a failed login, AFTER the server had issued
    // a perfectly good token. Degrading is strictly better: the pair is live in memory, so this
    // session works exactly as it should. The only thing lost is staying signed in across a
    // restart, and that is worth saying out loud rather than dying over.
    try {
      this.writeStored(next)
    } catch (e) {
      console.warn('[auth] could not persist the session; signed in for this run only', e)
    }
    this.schedule()
    this.listeners.forEach((l) => l(next))
    this.config.onChange?.(next)
  }

  private writeStored(next: TokenPair | null): void {
    if (!next) {
      this.config.session.write(null)
      void this.config.secrets?.write(null)
      return
    }
    const encrypted = !!this.config.secrets
    this.config.session.write(
      JSON.stringify({
        accessToken: next.accessToken,
        // Kept here ONLY when there is no encrypted store to put it in. On the desktop it goes to
        // the keychain instead, so the plain store never holds a 30-day credential.
        refreshToken: encrypted ? '' : next.refreshToken,
        expiresAt: next.expiresAt,
        accountId: next.accountId,
        email: next.email
      })
    )
    if (encrypted) void this.config.secrets?.write(next.refreshToken || null)
  }

  private readStored(): TokenPair | null {
    try {
      const raw = this.config.session.read()
      if (!raw) return null
      const p = JSON.parse(raw) as Partial<TokenPair>
      const held: TokenPair = {
        accessToken: p.accessToken || '',
        refreshToken: p.refreshToken || '',
        expiresAt: p.expiresAt || accessTokenExpiry(p.accessToken || ''),
        accountId: p.accountId || '',
        email: p.email || ''
      }
      // EVICT, do not merely ignore. Ignoring leaves it to be re-read by every other path that
      // looks at storage; removing it means the page shows a form once and is then truly clean.
      //
      // Two things are evictable. A token this platform can no longer USE — the opaque `sess_`
      // sessions that predate signed tokens, which no current daemon resolves. And a SPENT one:
      // expired with no refresh token behind it, which is the state a window opened by the desktop
      // app reaches when nothing has fed it for ten minutes. Keeping that one is how a page went
      // on presenting a dead credential, which the daemon does not refuse — it accepts the
      // reconnect ANONYMOUSLY, so the account's agents vanish with no error and no sign-in form.
      if (!usable(held.accessToken) || (!held.refreshToken && this.expired(held))) {
        this.config.session.write(null)
        return null
      }
      return held
    } catch {
      return null // private mode / storage disabled — sign-in works, it just will not persist
    }
  }

  private async readSecret(): Promise<string> {
    try {
      return (await this.config.secrets?.read()) || ''
    } catch {
      return ''
    }
  }

  // ------------------------------------------------------------------ plumbing

  private toPair(d: LoginResponse, fallbackEmail: string): TokenPair {
    const accessToken = String(d.access_token || d.token || d.session || '')
    return {
      accessToken,
      refreshToken: String(d.refresh_token || ''),
      // `expires_in` is RELATIVE on purpose (identity/domain/token.py): our clock and the server's
      // may disagree, and a relative lifetime is correct under skew where an absolute deadline is
      // not. The token's own `exp` covers a server that sends neither.
      expiresAt: d.expires_in
        ? Date.now() + Number(d.expires_in) * 1000
        : accessTokenExpiry(accessToken),
      accountId: String(d.account_id || ''),
      email: String(d.email || fallbackEmail)
    }
  }

  private async base(): Promise<string> {
    const clean = ((await this.config.accountsUrl()) || '').replace(/\/$/, '')
    if (!clean) throw new Error('no accounts service is configured')
    return clean
  }

  private deviceLabel(): string {
    try {
      return this.config.deviceLabel?.() || this.config.clientId
    } catch {
      return this.config.clientId
    }
  }

  private async send(url: string, body: unknown): Promise<Response> {
    const call = this.config.fetchImpl || fetch
    const ms = this.config.timeoutMs ?? DEFAULT_TIMEOUT_MS
    const ctl = typeof AbortController === 'function' ? new AbortController() : null
    const timer = setTimeout(() => ctl?.abort(), ms)
    try {
      return await call(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: ctl?.signal
      })
    } finally {
      clearTimeout(timer)
    }
  }

  private async post(url: string, body: unknown, what: string): Promise<unknown> {
    const r = await this.send(url, body)
    const text = await r.text()
    let data: Record<string, unknown> = {}
    try {
      data = text ? (JSON.parse(text) as Record<string, unknown>) : {}
    } catch {
      /* reported through the status check below */
    }
    if (!r.ok) {
      throw new Error(String(data?.detail || data?.error || `${what} failed (HTTP ${r.status})`))
    }
    return data
  }
}

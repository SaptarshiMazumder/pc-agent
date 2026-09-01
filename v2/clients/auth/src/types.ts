/**
 * The contract between this package and whoever hosts it. A leaf: imports nothing.
 *
 * Everything host-specific is a parameter. The agentd client resolves its accounts URL from
 * discovery and keeps secrets in the OS keychain; an agent window asks the daemon and has only
 * localStorage. Neither fact belongs in the renewal logic, and hard-coding either is what forced
 * a second implementation last time.
 */

/** The credential pair, plus who it belongs to. */
export interface TokenPair {
  /** Short-lived (~10 min). The ONLY half that ever travels on a connection. */
  accessToken: string
  /**
   * Long-lived (30 days), single-use, and rotating. Exchanged ONLY at `<accounts>/auth/refresh`.
   *
   * Empty is a legitimate state, not a broken one: a window opened by the desktop app is handed
   * an access token on its launch URL and deliberately never receives this one — it runs
   * third-party code, and this is a 30-day credential for the whole account. Such a window cannot
   * renew itself and is fed instead (`adopt`).
   */
  refreshToken: string
  /** Absolute epoch ms when `accessToken` dies. */
  expiresAt: number
  accountId: string
  email: string
}

/**
 * Where the refresh token is kept.
 *
 * Async because the desktop's answer is an IPC call to the OS keychain. The access token is NOT
 * stored here — see `SessionStore`.
 */
export interface SecretStore {
  read(): Promise<string | null>
  write(token: string | null): Promise<void>
}

/**
 * Where the non-secret half of the session is kept, synchronously.
 *
 * SYNCHRONOUS ON PURPOSE. An agent window is handed its credential on the launch URL, holds no
 * refresh token, and must survive a reload — so the access token has to be readable before the
 * first await. localStorage is the only store that shape works with.
 */
export interface SessionStore {
  read(): string | null
  write(value: string | null): void
}

/** What the accounts service answers with. Snake_case because that is the wire format. */
export interface LoginResponse {
  access_token?: string
  refresh_token?: string
  expires_in?: number
  account_id?: string
  email?: string
  /** Pre-token servers answered with one of these. Read so a new client still works on an old
   *  server; never written. */
  token?: string
  session?: string
}

export interface AuthConfig {
  /**
   * The accounts service base URL, no trailing slash. A RESOLVER, never a snapshot.
   *
   * It held a copied string once, set by whichever caller ran first — and one of them did not:
   * signing in fresh never configured it, so every later refresh returned null before making a
   * request. The symptom would have been a user signed out ten minutes after logging in, only if
   * they had signed in rather than resumed. A function cannot go stale and cannot be read too
   * early, and it picks up discovery resolving later for free.
   */
  accountsUrl: () => string | Promise<string>

  /** Sync store for the session. Required — every host has one. */
  session: SessionStore

  /** OS-encrypted store for the refresh token. Omit and it rides in `session` instead. */
  secrets?: SecretStore

  /** Names this client to the server, so `/me/devices` can tell them apart. */
  clientId: string

  /** A human-readable device name for the same list. Best-effort; never blocks sign-in. */
  deviceLabel?: () => string

  /**
   * Called on every change to the pair, including renewal and sign-out.
   *
   * This is how a host applies a new credential without this package knowing what a socket is.
   * The agentd client reconnects its gateway; an agent window swaps the token on the live socket
   * with `auth.update`, which is what lets a renewal happen mid-run without dropping it.
   */
  onChange?: (pair: TokenPair | null) => void

  /** Injected for tests. Defaults to global fetch. */
  /** BROWSER-ONLY. The refresh token lives in an HttpOnly cookie at the accounts service
   *  instead of anywhere this code can read: requests go out with credentials and ask the
   *  server for cookie mode, responses carry no refresh_token, and nothing durable is stored
   *  on this side at all. The desktop keeps its token in the runtime and never sets this. */
  cookies?: boolean
  fetchImpl?: typeof fetch

  timeoutMs?: number
}

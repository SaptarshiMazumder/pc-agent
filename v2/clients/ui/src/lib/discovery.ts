/**
 * Platform discovery — one baked URL in, every address out.
 *
 * WHY THIS EXISTS. Each build used to carry its own copy of every platform address: the desktop
 * flavors baked `accounts_url` + `model_proxy_url` + `ingest_url` into distribution.toml, the web
 * image baked VITE_AGENTD_ACCOUNTS_URL at build time. Those are ALB hostnames with an
 * AWS-assigned suffix, so any destroy/recreate mints new ones and every previously-shipped
 * artifact silently rots — which is exactly why the builds under clients/desktop/dist/ point at
 * three different long-dead load balancers.
 *
 * The consequence was not a broken build, which would at least be obvious. It was that signing in
 * on one client hit one accounts database and on another hit a different one, so THE SAME EMAIL
 * WAS TWO ACCOUNTS with two separate credit balances — and nothing anywhere said so.
 *
 * So: bake one `platform_url`, fetch `/.well-known/agentd-platform`, use what it says.
 *
 * Resolution is cached in sessionStorage and de-duplicated in flight, because several modules ask
 * for it during the first render and none of them should cost a round trip.
 */

export interface PlatformDoc {
  issuer: string
  authUrl: string
  jwksUri: string
  wsUrl: string
  modelProxyUrl: string
  providers: Array<{ id: string; label: string; kind: string }>
  tokenAuth: boolean
  accessTtlS: number
}

const SS_KEY = 'agentd.platform.doc'

let baked = '' // the single URL the build carries (desktop: flavor; web: env/query)
let resolved: PlatformDoc | null = null
let inflight: Promise<PlatformDoc | null> | null = null

/** Point discovery at a platform. Called once, before first render. */
export function configurePlatform(url: string): void {
  const next = (url || '').replace(/\/$/, '')
  if (next !== baked) {
    // Re-pointing invalidates everything: a cached document from another deployment is not a
    // fallback, it is the wrong stack's addresses.
    resolved = null
    inflight = null
    try {
      sessionStorage.removeItem(SS_KEY)
    } catch {
      /* private mode */
    }
  }
  baked = next
}

export function platformUrl(): string {
  const q = new URLSearchParams(typeof location !== 'undefined' ? location.search : '')
  const env = (import.meta as { env?: Record<string, string> }).env || {}
  return (q.get('platform') || env.VITE_AGENTD_PLATFORM_URL || baked).replace(/\/$/, '')
}

function parse(raw: Record<string, unknown>): PlatformDoc {
  const providers = Array.isArray(raw.providers) ? raw.providers : []
  return {
    issuer: String(raw.issuer || ''),
    authUrl: String(raw.auth_url || raw.issuer || ''),
    jwksUri: String(raw.jwks_uri || ''),
    wsUrl: String(raw.ws_url || ''),
    modelProxyUrl: String(raw.model_proxy_url || ''),
    providers: providers.map((p) => {
      const o = (p || {}) as Record<string, unknown>
      return {
        id: String(o.id || ''),
        label: String(o.label || ''),
        kind: String(o.kind || 'password')
      }
    }),
    tokenAuth: raw.token_auth !== false,
    accessTtlS: Number(raw.access_ttl_s || 600)
  }
}

/** The last resolved document, if any. Synchronous — never triggers a fetch. */
export function platformDoc(): PlatformDoc | null {
  if (resolved) return resolved
  try {
    const cached = sessionStorage.getItem(SS_KEY)
    if (cached) {
      const parsed = JSON.parse(cached) as { base: string; doc: PlatformDoc }
      if (parsed.base === platformUrl()) {
        resolved = parsed.doc
        return resolved
      }
    }
  } catch {
    /* unparseable cache — fall through to null and re-fetch */
  }
  return null
}

/**
 * Fetch the document (once). Resolves to null when there is nothing to discover or the
 * deployment cannot be reached — every caller must have a baked fallback, because a client that
 * cannot start offline is worse than one with a stale address.
 */
export function discoverPlatform(): Promise<PlatformDoc | null> {
  const base = platformUrl()
  if (!base) return Promise.resolve(null)
  const have = platformDoc()
  if (have) return Promise.resolve(have)
  if (inflight) return inflight

  inflight = fetch(`${base}/.well-known/agentd-platform`, { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((raw) => {
      if (!raw) return null
      resolved = parse(raw as Record<string, unknown>)
      try {
        sessionStorage.setItem(SS_KEY, JSON.stringify({ base, doc: resolved }))
      } catch {
        /* private mode — the in-memory copy still serves this session */
      }
      return resolved
    })
    .catch(() => null)
    .finally(() => {
      inflight = null
    })
  return inflight
}

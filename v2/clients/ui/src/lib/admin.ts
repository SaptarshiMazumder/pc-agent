/**
 * The admin control plane's client half.
 *
 * ONE ORIGIN, DELIBERATELY. Every call here goes to the accounts service, including the ones that
 * are really about creators or the registry — accounts proxies those. The alternative was calling
 * the publish Lambda and the S3 index straight from the browser, which means CORS on two more
 * origins and a mixed-content problem the day TLS lands on only some of them.
 *
 * NOTHING IS CACHED. An admin page that shows a stale balance or a stale creator state is worse
 * than one that takes an extra moment, because the whole point of the page is deciding what to do
 * about what it says. Every view re-reads on mount and after every mutation.
 *
 * Errors CARRY THE SERVER'S SENTENCE. The service already explains its refusals in words meant for
 * a person ("this account is an admin through deploy configuration and cannot be demoted here"),
 * so the UI shows that rather than inventing its own message from a status code.
 */

import { accountsUrl, currentAccessToken, isAccountsMode } from './auth'

export class AdminError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  if (!isAccountsMode()) throw new AdminError(0, 'This build has no platform account service.')
  // Ask for a token rather than reading the stored one: it refreshes when close to expiry, so a
  // long-lived dashboard tab does not start 401ing after ten minutes.
  const token = await currentAccessToken()
  if (!token) throw new AdminError(401, 'Sign in to use the admin console.')
  const r = await fetch(accountsUrl() + path, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {})
    }
  })
  const body = (await r.json().catch(() => ({}))) as Record<string, unknown>
  if (!r.ok) {
    throw new AdminError(r.status, String(body.detail || body.message || `HTTP ${r.status}`))
  }
  return body as T
}

const post = <T,>(path: string, body: unknown): Promise<T> =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

// --------------------------------------------------------------------------- who

export type WhoAmI = {
  account_id: string
  email: string
  is_admin: boolean
  /** 'config' = from deploy configuration and permanent; 'roster' = editable here. */
  source: 'config' | 'roster' | ''
}

/** Answers for everyone — a non-admin gets `is_admin: false`, not an error. */
export const whoami = (): Promise<WhoAmI> => call<WhoAmI>('/admin/whoami')

/**
 * Is the signed-in account an admin? For deciding whether to RENDER the nav entry.
 *
 * Cached per account for the life of the tab, because every component that wants to know asks on
 * mount and the answer only changes when someone is promoted — which they will not be mid-session
 * on their own screen. Deliberately NOT a security boundary: the server refuses every /admin/*
 * call on its own, and this only decides whether a menu item is drawn.
 *
 * Defaults to FALSE on any failure. A menu item that appears during an outage and 403s on click
 * is a worse experience than one that stays hidden.
 *
 * KEYED BY ACCOUNT ID, which is what makes sign-out safe without an explicit invalidation hook:
 * the next account asks a different question and gets a fresh answer. The only staleness left is
 * an admin promoted while their own tab is open, which resolves on reload.
 */
let cached: { accountId: string; isAdmin: boolean } | null = null

export async function isAdmin(accountId: string): Promise<boolean> {
  if (!accountId || !isAccountsMode()) return false
  if (cached?.accountId === accountId) return cached.isAdmin
  try {
    const me = await whoami()
    cached = { accountId, isAdmin: me.is_admin }
    return me.is_admin
  } catch {
    return false
  }
}

// --------------------------------------------------------------------------- overview

export type Overview = {
  month: string
  accounts_total: number
  accounts_active: number
  admins: number
  calls: number
  cost_usd: number
  in_tokens: number
  out_tokens: number
  cached_tokens: number
  credits_spent: number
  credits_outstanding: number
  top_agents: { agent_id: string; calls: number; cost_usd: number; in_tokens: number; out_tokens: number }[]
  top_accounts: { account_id: string; email: string; calls: number; cost_usd: number }[]
}

export const overview = (): Promise<Overview> => call<Overview>('/admin/overview')

// --------------------------------------------------------------------------- accounts

export type AccountRow = {
  account_id: string
  email: string
  created_at: number
  active: boolean
  budget_usd: number | null
  spent_usd: number
  credits_remaining: number
  admin_source: 'config' | 'roster' | ''
}

export type AccountList = { accounts: AccountRow[]; total: number; limit: number; offset: number }

export type Grant = {
  id: number
  scope: string
  credits: number
  credits_used: number
  credit_class: string
  model_tier_max: string
  expires_at: number
  created_at: number
}

export type UsageRow = {
  ts: number
  model: string
  agent_id: string
  in_tokens: number
  out_tokens: number
  cached_tokens: number
  cost_usd: number
  credits: number
  funding_source: string
}

export type Device = {
  family_id: string
  client_id: string
  device_label: string
  issued_at: number
  used_at: number
  expires_at: number
  revoked: boolean
}

export type AccountDetail = AccountRow & {
  over: boolean
  credits_enforced: boolean
  is_admin: boolean
  grants: Grant[]
  recent_usage: UsageRow[]
  usage_by_agent: { agent_id: string; calls: number; in_tokens: number; out_tokens: number; cost_usd: number }[]
  entitlements: { agent_id: string; source: string; min_version: string; expires_at: number; created_at: number }[]
  subscriptions: { product_id: string; status: string; renews_at: number; created_at: number }[]
  devices: Device[]
}

export const listAccounts = (q = '', limit = 50, offset = 0): Promise<AccountList> =>
  call<AccountList>(
    `/admin/accounts?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`
  )

export const accountDetail = (id: string): Promise<AccountDetail> =>
  call<AccountDetail>(`/admin/accounts/${encodeURIComponent(id)}`)

/** `null` clears the cap — which means UNLIMITED, not "restore a default". */
export const setBudget = (id: string, budget_usd: number | null): Promise<unknown> =>
  post(`/admin/accounts/${encodeURIComponent(id)}/budget`, { budget_usd })

export const setActive = (id: string, active: boolean): Promise<unknown> =>
  post(`/admin/accounts/${encodeURIComponent(id)}/active`, { active })

export const grantCredits = (
  id: string,
  credits: number,
  opts: { credit_class?: string; expires_days?: number; scope?: string } = {}
): Promise<unknown> => post(`/admin/accounts/${encodeURIComponent(id)}/credits`, { credits, ...opts })

export const revokeSessions = (
  id: string
): Promise<{ revoked: number; access_tokens_valid_for_s: number }> =>
  post(`/admin/accounts/${encodeURIComponent(id)}/sessions/revoke`, {})

export const setAdmin = (id: string, is_admin: boolean): Promise<unknown> =>
  post(`/admin/accounts/${encodeURIComponent(id)}/admin`, { is_admin })

export const setEntitlement = (
  id: string,
  agent_id: string,
  granted: boolean
): Promise<unknown> =>
  post(`/admin/accounts/${encodeURIComponent(id)}/entitlements`, { agent_id, granted })

// --------------------------------------------------------------------------- usage

export type UsageGroup = 'agent' | 'model' | 'account' | 'day'

export type UsageRollup = {
  month: string
  group_by: string
  months: string[]
  rows: {
    key: string
    calls: number
    in_tokens: number
    out_tokens: number
    cached_tokens: number
    credits: number
    cost_usd: number
  }[]
}

export const usage = (group: UsageGroup = 'agent', month = ''): Promise<UsageRollup> =>
  call<UsageRollup>(`/admin/usage?group_by=${group}&month=${encodeURIComponent(month)}`)

// --------------------------------------------------------------------------- money

export type Product = {
  id: string
  kind: string
  title: string
  creator_id: string
  agent_id: string
  price_usd: number
  credits: number
  scope: string
  model_tier_max: string
  period_days: number
  active: boolean
  subscribers: number
}

export const listProducts = (): Promise<{ products: Product[] }> =>
  call<{ products: Product[] }>('/admin/products')

export const saveProduct = (p: Partial<Product> & { id: string }): Promise<unknown> =>
  post('/admin/products', p)

export type Ledger = {
  accounts: Record<string, number>
  balanced: boolean
  residual_usd: number
  gross_margin_usd: number
  entries: {
    id: number
    ts: number
    txn_id: string
    txn_type: string
    account: string
    direction: string
    amount_usd: number
    account_id: string
    agent_id: string
    ref: string
  }[]
  total: number
}

export const ledger = (limit = 50, offset = 0): Promise<Ledger> =>
  call<Ledger>(`/admin/ledger?limit=${limit}&offset=${offset}`)

// --------------------------------------------------------------------------- catalog

export type Bundle = {
  id: string
  version: string
  title: string
  description: string
  publisher_id: string
  publisher_name: string
  publisher_revoked: boolean
  delivery: { web?: boolean; exe?: boolean }
  installers: unknown[]
  size: number
}

export type AgentsView = {
  configured: boolean
  registry_url: string
  schema?: number
  bundles: Bundle[]
  engine?: { version?: string; platform?: string; sha256?: string; url?: string }
  web?: { host?: string }
  roster?: { id: string; name: string; key: string; added: string }[]
  revoked?: string[]
  error?: string
}

export const agents = (): Promise<AgentsView> => call<AgentsView>('/admin/agents')

export type Creator = {
  creator_id: string
  account_id: string
  name: string
  state: string
  public_key: string
  created: string
  admitted: string
  revoked: string
  wrapped: boolean
  parked: { bundle_id: string; size: number; parked_at: string }[]
}

export const creators = (): Promise<{ creators: Creator[]; partial?: boolean }> =>
  call<{ creators: Creator[]; partial?: boolean }>('/admin/creators')

export const admitCreator = (creator_id?: string): Promise<{ message?: string }> =>
  post('/admin/creators/admit', creator_id ? { creator_id } : {})

export const revokeCreator = (creator_id: string): Promise<{ message?: string }> =>
  post('/admin/creators/revoke', { creator_id })

// --------------------------------------------------------------------------- keys

export type KeysView = {
  signing_keys: {
    kid: string
    alg: string
    active: boolean
    encrypted: boolean
    created_at: number
    expires_at: number
  }[]
  signing_key_kek: boolean
  secrets: {
    configured: boolean
    id: string
    last_changed?: string
    last_rotated?: string
    error?: string
    keys?: { name: string; set: boolean; placeholder: boolean; consumers: string[] }[]
  }
  creator_keys: {
    configured: boolean
    table: string
    kms_key: string
    error?: string
    keys?: {
      creator_id: string
      name: string
      state: string
      public_key: string
      created: string
      admitted: string
      wrapped: boolean
    }[]
  }
}

export const keys = (): Promise<KeysView> => call<KeysView>('/admin/keys')

export const rotateSigningKey = (): Promise<{ kid: string; previous_key_valid_for_s: number }> =>
  post('/admin/keys/signing/rotate', {})

export const setSecret = (
  name: string,
  value: string
): Promise<{ rolled: string[]; roll_errors: string[]; in_effect: boolean; note: string }> =>
  post('/admin/keys/secret', { name, value })

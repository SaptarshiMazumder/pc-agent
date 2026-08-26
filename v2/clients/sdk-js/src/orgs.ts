/**
 * Organizations and seats, for an agent window.
 *
 * COPIED FROM clients/ui/src/lib/orgs.ts — the assistant's own client, function for function. An
 * enterprise buys seats once; an agent must not have a second, subtly different idea of what a
 * seat is, who may mint an invite, or what "joinable" means.
 *
 * NOTHING NEW IS PLUMBED HERE, exactly as in `credits.ts`. A window already knows who the user is
 * (`identity()`, an auto-refreshing TokenManager) and where the accounts service lives
 * (`accountsUrl()`, answered by the daemon that served the page). Those are the only two things
 * these calls need.
 *
 * EVERY ANSWER IS ALREADY SCOPED SERVER-SIDE by membership — the API fails closed with a 404 for
 * an org you are not in — so nothing here filters. It renders what the server says the caller may
 * see. A client that decided for itself which orgs to show would be a second, weaker copy of an
 * access rule that is already enforced where it matters.
 *
 * THE DAEMON IS NOT INVOLVED. Org membership reaches it through the access token's own `orgs`
 * claim, not through anything this module does.
 */

import { identity } from './identity'
import { accountsUrl, type DaemonOptions } from './platform-status'
import type { AgentdClient } from './client'

export interface OrgOptions extends DaemonOptions {
  client?: AgentdClient
  storageKey?: string
}

export type OrgMembership = { id: string; name: string; role: string }
export type JoinableOrg = { id: string; name: string }
export type MyOrgs = { orgs: OrgMembership[]; joinable: JoinableOrg[] }

export type OrgMember = {
  accountId: string
  email: string
  role: string
  monthlyCreditCap: number
  addedAt: number
}

export type OrgDetail = {
  id: string
  name: string
  role: string
  seatsTotal: number
  seatsUsed: number
  createdAt: number
  /** admin-view extras — absent for a plain member, exactly as the server withholds them */
  members?: OrgMember[]
  domains?: string[]
  primaryOwner?: string
  poolCreditsRemaining?: number
}

export type OrgUsageRow = {
  accountId: string
  email: string
  credits: number
  costUsd: number
  calls: number
  monthlyCreditCap: number
}

export type OrgInvite = {
  inviteToken: string
  orgId: string
  orgName: string
  email: string
  role: string
  expiresAt: number
}

async function call<T>(
  opts: OrgOptions,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
): Promise<T> {
  const base = await accountsUrl(opts)
  // NOT a silent empty result. No accounts service means orgs cannot exist on this install, and a
  // page that renders "you have no organizations" would be stating something it does not know.
  if (!base) throw new Error('this daemon has no accounts service, so organizations are unavailable')
  const token = await identity(opts).accessToken()
  if (!token) throw new Error('sign in first')
  const r = await fetch(base + path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
  const d = (await r.json().catch(() => ({}))) as Record<string, unknown>
  // The server's own words. "no seats left (5 of 5 in use)" is the whole explanation a user needs,
  // and replacing it with a status code is how a solvable problem becomes a support ticket.
  if (!r.ok) throw new Error(String(d.detail || `request failed (HTTP ${r.status})`))
  return d as T
}

function toDetail(d: Record<string, unknown>): OrgDetail {
  const out: OrgDetail = {
    id: String(d.id || ''),
    name: String(d.name || ''),
    role: String(d.role || 'member'),
    seatsTotal: Number(d.seats_total || 0),
    seatsUsed: Number(d.seats_used || 0),
    createdAt: Number(d.created_at || 0),
  }
  // PRESENCE IS THE PERMISSION. The server sends `members` only to an admin, so this is not a
  // convenience check — it is how the client learns which view it is allowed to draw.
  if (Array.isArray(d.members)) {
    out.members = (d.members as Record<string, unknown>[]).map((m) => ({
      accountId: String(m.account_id || ''),
      email: String(m.email || ''),
      role: String(m.role || 'member'),
      monthlyCreditCap: Number(m.monthly_credit_cap || 0),
      addedAt: Number(m.added_at || 0),
    }))
    out.domains = (d.domains as string[]) || []
    out.primaryOwner = String(d.primary_owner || '')
    out.poolCreditsRemaining = Number(d.pool_credits_remaining || 0)
  }
  return out
}

/** My orgs + my role, and the ones my email domain would let me join. */
export async function fetchMyOrgs(opts: OrgOptions = {}): Promise<MyOrgs> {
  const d = await call<{ orgs?: Record<string, unknown>[]; joinable?: Record<string, unknown>[] }>(
    opts,
    'GET',
    '/me/orgs',
  )
  return {
    orgs: (d.orgs || []).map((o) => ({
      id: String(o.id || ''),
      name: String(o.name || o.id || ''),
      role: String(o.role || 'member'),
    })),
    joinable: (d.joinable || []).map((o) => ({
      id: String(o.id || ''),
      name: String(o.name || o.id || ''),
    })),
  }
}

export async function createOrg(
  name: string,
  seatsTotal?: number,
  opts: OrgOptions = {},
): Promise<OrgDetail> {
  return toDetail(
    await call(opts, 'POST', '/orgs', { name, ...(seatsTotal ? { seats_total: seatsTotal } : {}) }),
  )
}

/** Join by invite token OR by the domain offer (an org id from `joinable`). */
export async function joinOrg(
  input: { inviteToken?: string; orgId?: string },
  opts: OrgOptions = {},
): Promise<OrgDetail> {
  return toDetail(
    await call(opts, 'POST', '/orgs/join', {
      ...(input.inviteToken ? { invite_token: input.inviteToken } : {}),
      ...(input.orgId ? { org_id: input.orgId } : {}),
    }),
  )
}

export async function fetchOrgDetail(orgId: string, opts: OrgOptions = {}): Promise<OrgDetail> {
  return toDetail(await call(opts, 'GET', `/orgs/${encodeURIComponent(orgId)}`))
}

export async function mintInvite(
  orgId: string,
  input: { email?: string; role?: string } = {},
  opts: OrgOptions = {},
): Promise<OrgInvite> {
  const d = await call<Record<string, unknown>>(
    opts,
    'POST',
    `/orgs/${encodeURIComponent(orgId)}/invites`,
    { ...(input.email ? { email: input.email } : {}), ...(input.role ? { role: input.role } : {}) },
  )
  return {
    inviteToken: String(d.invite_token || ''),
    orgId: String(d.org_id || orgId),
    orgName: String(d.org_name || ''),
    email: String(d.email || ''),
    role: String(d.role || 'member'),
    expiresAt: Number(d.expires_at || 0),
  }
}

/** Role change / monthly cap / remove (`active: false`) — org admin and up, server-enforced. */
export async function updateMember(
  orgId: string,
  accountId: string,
  patch: { role?: string; monthlyCreditCap?: number; active?: boolean },
  opts: OrgOptions = {},
): Promise<OrgDetail> {
  return toDetail(
    await call(
      opts,
      'POST',
      `/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(accountId)}`,
      {
        ...(patch.role !== undefined ? { role: patch.role } : {}),
        ...(patch.monthlyCreditCap !== undefined
          ? { monthly_credit_cap: patch.monthlyCreditCap }
          : {}),
        ...(patch.active !== undefined ? { active: patch.active } : {}),
      },
    ),
  )
}

export async function updateDomain(
  orgId: string,
  domain: string,
  remove = false,
  opts: OrgOptions = {},
): Promise<OrgDetail> {
  return toDetail(
    await call(opts, 'POST', `/orgs/${encodeURIComponent(orgId)}/domains`, { domain, remove }),
  )
}

export async function fetchOrgUsage(
  orgId: string,
  opts: OrgOptions = {},
): Promise<{ month: string; members: OrgUsageRow[] }> {
  const d = await call<{ month?: string; members?: Record<string, unknown>[] }>(
    opts,
    'GET',
    `/orgs/${encodeURIComponent(orgId)}/usage`,
  )
  return {
    month: String(d.month || ''),
    members: (d.members || []).map((m) => ({
      accountId: String(m.account_id || ''),
      email: String(m.email || ''),
      credits: Number(m.credits || 0),
      costUsd: Number(m.cost_usd || 0),
      calls: Number(m.calls || 0),
      monthlyCreditCap: Number(m.monthly_credit_cap || 0),
    })),
  }
}

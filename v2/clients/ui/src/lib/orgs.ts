/**
 * Organizations — the client half of enterprise tenancy (plan E5).
 *
 * Everything here talks to the ACCOUNTS service with the caller's own bearer token; every
 * answer is already scoped server-side by membership (fail-closed 404s), so this module does
 * no filtering of its own — it renders what the server says the caller may see, exactly the
 * shape rule lib/admin.ts follows. The daemon is not involved: org membership reaches IT via
 * the token's own claim, not via anything this module does.
 */

import { useEffect, useState } from 'react'

import { accountsUrl, currentAccessToken, isAccountsMode, useAuthSession } from './auth'

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

async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const token = await currentAccessToken()
  if (!token || !isAccountsMode()) throw new Error('sign in first')
  const r = await fetch(accountsUrl() + path, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {})
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {})
  })
  const d = (await r.json().catch(() => ({}))) as Record<string, unknown>
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
    createdAt: Number(d.created_at || 0)
  }
  if (Array.isArray(d.members)) {
    out.members = (d.members as Record<string, unknown>[]).map((m) => ({
      accountId: String(m.account_id || ''),
      email: String(m.email || ''),
      role: String(m.role || 'member'),
      monthlyCreditCap: Number(m.monthly_credit_cap || 0),
      addedAt: Number(m.added_at || 0)
    }))
    out.domains = (d.domains as string[]) || []
    out.primaryOwner = String(d.primary_owner || '')
    out.poolCreditsRemaining = Number(d.pool_credits_remaining || 0)
  }
  return out
}

/** My orgs + my role (the switcher's data), and the ones my email domain could join. */
export async function fetchMyOrgs(): Promise<MyOrgs> {
  const d = await call<{ orgs?: Record<string, unknown>[]; joinable?: Record<string, unknown>[] }>(
    'GET',
    '/me/orgs'
  )
  return {
    orgs: (d.orgs || []).map((o) => ({
      id: String(o.id || ''),
      name: String(o.name || o.id || ''),
      role: String(o.role || 'member')
    })),
    joinable: (d.joinable || []).map((o) => ({
      id: String(o.id || ''),
      name: String(o.name || o.id || '')
    }))
  }
}

// The last-fetched memberships, so a remount (the sidebar collapses and reopens, a view
// swaps) renders the org row immediately instead of blinking it in after a round trip. Primed
// by useMyOrgs below; cleared implicitly on sign-out because the hook returns [] when there is
// no session and refetches on the next one.
let cachedMemberships: OrgMembership[] | null = null

/**
 * React hook: the signed-in account's org memberships, [] while signed out or still loading.
 *
 * This is the NAV's question — "does this person belong to an organization at all?" — asked by
 * the sidebar to decide whether an Organization row exists. Pages that need the full answer
 * (joinable offers, roles for admin controls) keep calling fetchMyOrgs themselves; this hook
 * is deliberately just the membership list, cached across mounts.
 */
export function useMyOrgs(): OrgMembership[] {
  const session = useAuthSession()
  const [orgs, setOrgs] = useState<OrgMembership[]>(session ? (cachedMemberships ?? []) : [])
  useEffect(() => {
    if (!session) {
      // Null the cross-mount cache too, not just local state: it is a MODULE GLOBAL, so leaving it
      // set lets the NEXT account (after a sign-out then sign-in) inherit this account's orgs — and
      // the catch below would even fall back to it when that account's own fetch fails or is slow.
      // That is the "the old org/domain stayed after I switched users" bleed.
      cachedMemberships = null
      setOrgs([])
      return
    }
    let live = true
    fetchMyOrgs()
      .then((d) => {
        cachedMemberships = d.orgs
        if (live) setOrgs(d.orgs)
      })
      .catch(() => {
        // Signed in but accounts unreachable: keep whatever we had rather than flashing the
        // row away — the nav disappearing is worse than it being one fetch stale.
        if (live && cachedMemberships) setOrgs(cachedMemberships)
      })
    return () => {
      live = false
    }
  }, [session])
  return orgs
}

export async function createOrg(name: string, seatsTotal?: number): Promise<OrgDetail> {
  return toDetail(
    await call('POST', '/orgs', { name, ...(seatsTotal ? { seats_total: seatsTotal } : {}) })
  )
}

/** Join by invite token OR by the domain offer (org id from the login/joinable list). */
export async function joinOrg(input: { inviteToken?: string; orgId?: string }): Promise<OrgDetail> {
  return toDetail(
    await call('POST', '/orgs/join', {
      ...(input.inviteToken ? { invite_token: input.inviteToken } : {}),
      ...(input.orgId ? { org_id: input.orgId } : {})
    })
  )
}

export async function fetchOrgDetail(orgId: string): Promise<OrgDetail> {
  return toDetail(await call('GET', `/orgs/${encodeURIComponent(orgId)}`))
}

export async function mintInvite(
  orgId: string,
  input: { email?: string; role?: string } = {}
): Promise<OrgInvite> {
  const d = await call<Record<string, unknown>>(
    'POST',
    `/orgs/${encodeURIComponent(orgId)}/invites`,
    { ...(input.email ? { email: input.email } : {}), ...(input.role ? { role: input.role } : {}) }
  )
  return {
    inviteToken: String(d.invite_token || ''),
    orgId: String(d.org_id || orgId),
    orgName: String(d.org_name || ''),
    email: String(d.email || ''),
    role: String(d.role || 'member'),
    expiresAt: Number(d.expires_at || 0)
  }
}

/** Role change / monthly cap / remove (active:false) — org admin+ only, server-enforced. */
export async function updateMember(
  orgId: string,
  accountId: string,
  patch: { role?: string; monthlyCreditCap?: number; active?: boolean }
): Promise<OrgDetail> {
  return toDetail(
    await call('POST', `/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(accountId)}`, {
      ...(patch.role !== undefined ? { role: patch.role } : {}),
      ...(patch.monthlyCreditCap !== undefined
        ? { monthly_credit_cap: patch.monthlyCreditCap }
        : {}),
      ...(patch.active !== undefined ? { active: patch.active } : {})
    })
  )
}

export async function updateDomain(
  orgId: string,
  domain: string,
  remove = false
): Promise<OrgDetail> {
  return toDetail(
    await call('POST', `/orgs/${encodeURIComponent(orgId)}/domains`, { domain, remove })
  )
}

export async function fetchOrgUsage(orgId: string): Promise<{ month: string; members: OrgUsageRow[] }> {
  const d = await call<{ month?: string; members?: Record<string, unknown>[] }>(
    'GET',
    `/orgs/${encodeURIComponent(orgId)}/usage`
  )
  return {
    month: String(d.month || ''),
    members: (d.members || []).map((m) => ({
      accountId: String(m.account_id || ''),
      email: String(m.email || ''),
      credits: Number(m.credits || 0),
      costUsd: Number(m.cost_usd || 0),
      calls: Number(m.calls || 0),
      monthlyCreditCap: Number(m.monthly_credit_cap || 0)
    }))
  }
}

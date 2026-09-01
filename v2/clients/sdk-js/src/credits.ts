/**
 * Credits, for an agent window — the data half. The UI half is `wallet.ts`.
 *
 * NOTHING NEW IS PLUMBED HERE. An agent window already knows who the user is (`identity()`, an
 * auto-refreshing TokenManager) and where the accounts service lives (`accountsUrl()`, answered by
 * the daemon it is served from). Those are exactly the two questions `BillingClient` asks its host,
 * so this file is the wiring and not an implementation — the implementation is `@agentd/billing`,
 * shared byte-for-byte with the agentd client so an agent and the desktop app cannot disagree
 * about what a purchase is.
 */

import { BillingClient, type BillingHost } from '@agentd/billing'

import { identity } from './identity'
import { accountsUrl, type DaemonOptions } from './platform-status'
import type { AgentdClient } from './client'

export interface CreditsOptions extends DaemonOptions {
  client?: AgentdClient
  storageKey?: string
}

/**
 * `crypto.randomUUID` is absent on insecure origins and in older webviews, and an agent window is
 * frequently both. A purchase without an idempotency key is a double-click away from two charges,
 * so this must never be the thing that is missing.
 */
function newKey(): string {
  const c: any = typeof crypto === 'undefined' ? null : crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  if (c && typeof c.getRandomValues === 'function') {
    const b = c.getRandomValues(new Uint8Array(16))
    return Array.from(b, (n: number) => n.toString(16).padStart(2, '0')).join('')
  }
  return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
}

/** The host answers, for a page served by a daemon. */
export function creditsHost(opts: CreditsOptions = {}): BillingHost {
  return {
    accountsUrl: () => accountsUrl(opts),
    accessToken: () => identity(opts).accessToken(),
    newKey
  }
}

/** A ready-to-use billing client for this window. */
export function billing(opts: CreditsOptions = {}): BillingClient {
  return new BillingClient(creditsHost(opts))
}

export {
  BillingClient,
  notifyCreditsChanged,
  onCreditsChanged,
  type BillingHost,
  type Catalog,
  type CreditPack,
  type Credits,
  type Purchase
} from '@agentd/billing'

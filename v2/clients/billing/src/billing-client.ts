/**
 * BillingClient — read a balance, read the shelf, buy from it. The only code that talks money to
 * the accounts service.
 *
 * WHY IT IS A CLASS TAKING A HOST rather than four free functions. Three very different callers
 * need this: the agentd renderer (its own TokenManager, its own configured accounts URL), an agent
 * window (the SDK's `identity()` and a URL discovered from the daemon), and Agent Builder (both,
 * via the SDK). Every one of them answers "where is accounts" and "what is my token" differently,
 * and NONE of them differs in what a purchase is. Injecting those two answers is what lets the
 * third caller be free rather than a third implementation — the same argument that produced
 * `@agentd/auth`.
 *
 * IT BUYS THROUGH /me/checkout, NOT /me/purchase. `/me/checkout` is a strict superset: on a rail
 * that settles in place it returns the completed purchase, and on a card rail it returns a link to
 * go and pay. Building on it means an agent shipped today keeps working the day a real rail is
 * switched on, with no change to the agent.
 *
 * THE ONLY THING A CLIENT MAY SEND IS A product_id. Price and credit count are read server-side
 * from the products row — otherwise a user posts their own numbers and mints a fortune. That rule
 * is enforced by the server; it is repeated here so nobody "helpfully" adds an amount parameter.
 *
 * READS FAIL SOFT, THE PURCHASE FAILS LOUD. A balance that cannot be fetched renders as "unknown",
 * which is honest and harmless. A purchase that fails must reach the user with the server's own
 * words — silently resolving it would leave someone believing they had bought credits.
 */

import { notifyCreditsChanged } from './credits-bus'
import type { BillingHost, Catalog, CreditPack, Credits, Purchase } from './types'

function toPack(d: Record<string, unknown>): CreditPack {
  return {
    id: String(d.id || ''),
    kind: String(d.kind || ''),
    title: String(d.title || ''),
    priceUsd: Number(d.price_usd || 0),
    credits: Number(d.credits || 0),
    seats: Number(d.seats || 0),
    modelTierMax: String(d.model_tier_max || ''),
    periodDays: Number(d.period_days || 0)
  }
}

export class BillingClient {
  constructor(private readonly host: BillingHost) {}

  private async base(): Promise<string> {
    const url = String((await this.host.accountsUrl()) || '').replace(/\/$/, '')
    if (!url) throw new Error('this daemon has no accounts service configured')
    return url
  }

  private async authed(): Promise<Record<string, string>> {
    const token = String((await this.host.accessToken()) || '')
    if (!token) throw new Error('sign in first')
    return { Authorization: `Bearer ${token}` }
  }

  /**
   * The balance, or null when there is nothing to show — not signed in, no accounts service, or
   * the request failed. Null is rendered as "unavailable" rather than as zero, because showing a
   * confident 0 to someone with credits is worse than admitting we do not know.
   */
  async credits(agentId = ''): Promise<Credits | null> {
    try {
      const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
      const r = await fetch(`${await this.base()}/me/credits${q}`, { headers: await this.authed() })
      if (!r.ok) return null
      const d = (await r.json()) as Record<string, unknown>
      return {
        creditsRemaining: Number(d.credits_remaining || 0),
        fundingSource: String(d.funding_source || ''),
        orgId: String(d.org_id || ''),
        memberCapped: Boolean(d.member_capped),
        creditClass: String(d.credit_class || ''),
        modelTierMax: String(d.model_tier_max || ''),
        entitlementRequired: Boolean(d.entitlement_required),
        entitled: d.entitled !== false,
        expiresAt: Number(d.expires_at || 0)
      }
    } catch {
      return null
    }
  }

  /**
   * What is for sale. NOT signed-in-only and NOT hardcoded: the packs come from the `products`
   * table, whose prices derive from the markup dial, so changing what is on sale is a row in a
   * database and never a release of a client — and the price shown cannot drift from the price
   * charged, because there is only one of them.
   */
  async catalog(kind = 'credit_pack'): Promise<Catalog | null> {
    try {
      const r = await fetch(`${await this.base()}/products?kind=${encodeURIComponent(kind)}`)
      if (!r.ok) return null
      const d = (await r.json()) as {
        products?: Record<string, unknown>[]
        provider?: string
        payment_note?: string
      }
      return {
        packs: (d.products || []).map(toPack),
        provider: String(d.provider || ''),
        paymentNote: String(d.payment_note || '')
      }
    } catch {
      return null
    }
  }

  /**
   * Buy a pack. THROWS with the server's own message on refusal.
   *
   * `returnUrl` is only consulted by a rail that sends the customer away; on one that settles in
   * place it is ignored, and the returned `checkoutUrl` is empty. The DEFAULT return is the
   * accounts service's own neutral "checkout finished" page, NOT the caller's URL: a surface's
   * own href drags its whole query string — session token included — through the rail's redirect
   * and into browser history, and on desktop it reopens the app in a browser tab instead of the
   * window the purchase started in. The purchase's real conclusion never travels through that
   * tab anyway — it arrives on the webhook, and `awaitGrant` is what tells the initiating window.
   */
  async buy(productId: string, returnUrl = '', orgId = ''): Promise<Purchase> {
    const base = await this.base()
    const body: Record<string, unknown> = {
      product_id: productId,
      idempotency_key: this.host.newKey()
    }
    // Buying FOR an organization — seats, or a pool top-up. The server checks the buyer is an
    // owner/admin of it; this only names which purchase it is.
    if (orgId) body.org_id = orgId
    const back = returnUrl || `${base}/checkout/complete`
    body.success_url = back
    body.cancel_url = back
    const r = await fetch(`${base}/me/checkout`, {
      method: 'POST',
      headers: { ...(await this.authed()), 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    const d = (await r.json().catch(() => ({}))) as Record<string, unknown>
    if (!r.ok) throw new Error(String(d.detail || `purchase failed (HTTP ${r.status})`))

    const checkoutUrl = String(d.checkout_url || '')
    // Only announce a balance change when one actually happened. A pending checkout has moved
    // nothing yet, and telling every listener otherwise makes each of them re-read an unchanged
    // number and show the user a balance that contradicts the screen they are looking at.
    if (!checkoutUrl) notifyCreditsChanged()

    const payment = (d.payment || {}) as Record<string, unknown>
    return {
      ok: true,
      replayed: d.replayed === true,
      credits: Number(d.credits || 0),
      priceUsd: Number(d.price_usd || 0),
      creditsRemaining: Number(d.credits_remaining || 0),
      paymentDetail: String(payment.detail || ''),
      checkoutUrl
    }
  }

  /**
   * Watch for a checkout's credits to land, then ring the credits bus.
   *
   * A card purchase finishes on a WEBHOOK, in another tab, minutes later — nothing tells the
   * window that started it. This polls the balance until it RISES (a grant adds; concurrent
   * spending only subtracts, so a rise is unambiguous), then fires `notifyCreditsChanged()` so
   * every listening view refreshes itself — the window the purchase began in included.
   *
   * Resolves true when the grant landed, false when the customer walked away (timeout). A false
   * is "nothing happened", never an error — an abandoned checkout costs nothing and grants
   * nothing, and the next purchase starts clean.
   */
  async awaitGrant(opts: { agentId?: string; timeoutMs?: number; pollMs?: number } = {}): Promise<boolean> {
    const { agentId = '', timeoutMs = 180_000, pollMs = 4_000 } = opts
    const baseline = (await this.credits(agentId))?.creditsRemaining
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, pollMs))
      const now = (await this.credits(agentId))?.creditsRemaining
      if (now !== undefined && (baseline === undefined || now > baseline)) {
        notifyCreditsChanged()
        return true
      }
    }
    return false
  }
}

/* Credits & billing — agentd's page, copied.
 *
 * COPIED FROM clients/ui/src/components/SubscriptionView.tsx. Same balance card, same
 * catalogue-driven packs, same receipt, same disclosure. An agent's shop and the assistant's are
 * one screen, because they are one product.
 *
 * WHAT THIS REPLACED. Agents used to mount `mountCreditsPanel` — a 287-line vanilla-DOM panel in
 * the SDK, written for the vanilla templates that no longer exist. agentd never used it; it has
 * always had this React page. So there were two credit screens: the one the user sees in the
 * assistant, and a different one every generated agent shipped. One of them had to go, and it was
 * never going to be the one agentd uses.
 *
 * THE PACKS ARE NOT IN THIS FILE. They come from the accounts service's product catalogue, so
 * changing what is for sale is a row in a database, never a release of this agent — and the price
 * shown cannot drift from the price charged, because there is only one of them.
 *
 * THE MONEY LOGIC IS NOT IN THIS FILE EITHER. `BillingClient` (@agentd/billing) owns idempotency
 * keys, refusals and "has the money actually arrived yet", and the assistant uses the SAME client.
 * This is a view over it. That is what makes copying it safe: nothing about a purchase is
 * reimplemented here.
 */

import { billing as shop, onCreditsChanged } from '@agentd/client'
import type { Catalog, CreditPack, Credits as Balance } from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'

import './credits.css'

export default function Credits({ agentId = '' }: { agentId?: string }) {
  const [balance, setBalance] = useState<Balance | null>(null)
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState('')

  const refresh = useCallback(async (): Promise<void> => {
    setBalance(await shop().credits(agentId))
  }, [agentId])

  useEffect(() => {
    void refresh()
    void shop().catalog().then(setCatalog)
    // A purchase made anywhere — another window, the assistant — moves this balance too.
    return onCreditsChanged(() => void refresh())
  }, [refresh])

  async function buy(pack: CreditPack): Promise<void> {
    setBusy(pack.id)
    setError('')
    setReceipt('')
    try {
      const r = await shop().buy(pack.id, location.href.split('#')[0])
      // A card rail answers with somewhere to go rather than a completed purchase.
      if (r.checkoutUrl) {
        location.href = r.checkoutUrl
        return
      }
      setBalance((c) => (c ? { ...c, creditsRemaining: r.creditsRemaining } : c))
      setReceipt(
        r.replayed
          ? `Already bought — you have ${r.creditsRemaining.toLocaleString()} credits.`
          : `Added ${r.credits.toLocaleString()} credits. Balance: ${r.creditsRemaining.toLocaleString()}. ${r.paymentDetail}`,
      )
      // The authoritative balance, in case a concurrent run spent some mid-purchase.
      void refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="credits-page">
      <div className="credits-head">
        <div className="credits-title">Credits &amp; billing</div>
        <div className="credits-sub">
          Credits pay for model calls on your account. Buy more at any time.
        </div>
      </div>

      <div className="credits-body">
        <div className="credits-group">
          <div className="credits-section">Balance</div>
          <div className="plan-card">
            <div className="plan-top">
              <span className="plan-name">
                {balance ? `${balance.creditsRemaining.toLocaleString()} credits` : 'checking…'}
              </span>
              <button
                className="cbtn"
                type="button"
                onClick={() => void refresh()}
                title="Re-read your balance from the platform"
              >
                Refresh
              </button>
            </div>
            <div className="plan-price">
              {balance?.fundingSource === 'org_pool'
                ? 'Your organization’s shared pool'
                : balance?.fundingSource === 'agent_subscription'
                  ? 'Funded by an agent subscription'
                  : 'Your platform balance'}
              {balance?.creditClass === 'promotional' ? <span> · promotional</span> : null}
              {balance?.modelTierMax ? <span> · up to the “{balance.modelTierMax}” tier</span> : null}
            </div>
            {balance?.memberCapped && (
              <ul className="plan-feats">
                <li>
                  Your seat’s monthly allowance is spent. The pool may still hold credits — an
                  organization admin can raise your allowance.
                </li>
              </ul>
            )}
            {balance?.creditsRemaining === 0 && !balance?.memberCapped && (
              <ul className="plan-feats">
                <li>
                  {balance?.fundingSource === 'org_pool'
                    ? 'The organization’s pool is empty — an admin can top it up from the Organizations page.'
                    : 'Out of credits — messages are refused until you top up.'}
                </li>
              </ul>
            )}
          </div>
        </div>

        {/* NO STORE FOR AN ORG MEMBER. Their turns can only spend the organization's pool, and
            the server refuses a personal purchase with exactly that explanation — so offering the
            packs here would sell a thing the buyer could never use. Admins buy for the org from
            the Organizations page, which is where the seats live too. */}
        {balance?.orgId ? (
          <div className="credits-group">
            <div className="credits-section">Buying credits</div>
            <div className="credits-note">
              Your organization funds your usage. An organization owner or admin can top up the
              pool and buy seats from the Organizations page.
            </div>
          </div>
        ) : (
        <div className="credits-group">
          <div className="credits-section">Buy credits</div>
          {catalog === null ? (
            <div className="credits-note">Loading the store…</div>
          ) : catalog.packs.length === 0 ? (
            <div className="credits-note">
              No credit packs are configured on this environment. They come from the accounts
              service’s product catalogue.
            </div>
          ) : (
            <>
              <div className="pack-grid">
                {catalog.packs.map((p) => (
                  <div className="pack" key={p.id}>
                    <div className="pack-name">{p.credits.toLocaleString()} credits</div>
                    <div className="pack-by">
                      ${p.priceUsd.toFixed(2)}
                      {p.periodDays > 0 ? ` · expires after ${p.periodDays} days` : ''}
                    </div>
                    <p className="pack-desc">
                      {p.title || `${p.credits.toLocaleString()} credits`}
                      {p.modelTierMax ? ` · up to the “${p.modelTierMax}” tier` : ''}
                    </p>
                    <button
                      className="cbtn primary"
                      type="button"
                      disabled={!!busy}
                      onClick={() => void buy(p)}
                      title={`Add ${p.credits.toLocaleString()} credits for $${p.priceUsd.toFixed(2)}`}
                    >
                      {busy === p.id ? 'Adding…' : `Buy · $${p.priceUsd.toFixed(2)}`}
                    </button>
                  </div>
                ))}
              </div>
              {/* The rail's own disclosure, verbatim. Not this file's opinion — wiring up a real
                  card rail rewrites this sentence instead of leaving a stale "nothing is charged"
                  note on a page that now charges. */}
              {catalog.paymentNote && <div className="credits-note">{catalog.paymentNote}</div>}
            </>
          )}
          {receipt && <div className="credits-note">{receipt}</div>}
          {error && <div className="credits-note error">{error}</div>}
        </div>
        )}
      </div>
    </div>
  )
}

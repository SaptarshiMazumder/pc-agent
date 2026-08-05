import { useEffect, useState } from 'react'
import { Check, CreditCard, RefreshCw, Sparkles, Zap } from 'lucide-react'

import {
  fetchCatalog,
  fetchCredits,
  purchase,
  useAuthSession,
  type Catalog,
  type CreditPack,
  type Credits
} from '../lib/auth'
import { useBilling } from '../lib/billing'
import { useApp } from '../state/store'
import PageShell from './PageShell'

/**
 * Credits & billing — the balance, and the shelf you top it up from.
 *
 * THE PACKS ARE NOT IN THIS FILE. They come from GET /products?kind=credit_pack, which reads the
 * `products` table, whose prices are derived from the markup dial. So changing what is for sale is
 * a row in a database (or one env var), never a release of the client — and the price shown here
 * cannot drift from the price charged, because there is only one of them.
 *
 * Payments are mocked today. The disclosure for that comes from the SERVER (`paymentNote`, the
 * rail's own words) rather than being hardcoded here, so wiring up a real rail rewrites this
 * page's promises instead of leaving a stale "no card is charged" note on a page that now charges.
 *
 * Local (BYOK) mode has no account and no credits, so the whole billing half is hidden there and
 * the old free-plan card is what remains.
 */
export default function SubscriptionView() {
  const flavor = useApp((s) => s.flavor)
  const session = useAuthSession()
  const { billing } = useBilling()

  const [credits, setCredits] = useState<Credits | null>(null)
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState('')

  async function refresh(): Promise<void> {
    setCredits(await fetchCredits())
  }

  useEffect(() => {
    if (!billing) {
      setCredits(null)
      setCatalog(null)
      return
    }
    void refresh()
    void fetchCatalog().then(setCatalog)
  }, [billing, session?.accountId])

  async function buy(pack: CreditPack): Promise<void> {
    setBusy(pack.id)
    setError('')
    setReceipt('')
    try {
      const r = await purchase(pack.id)
      setCredits((c) => (c ? { ...c, creditsRemaining: r.creditsRemaining } : c))
      setReceipt(
        r.replayed
          ? `Already bought — you have ${r.creditsRemaining.toLocaleString()} credits.`
          : `Added ${r.credits.toLocaleString()} credits. Balance: ${r.creditsRemaining.toLocaleString()}. ${r.paymentDetail}`
      )
      // The authoritative balance, in case a concurrent message spent some mid-purchase.
      void refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <PageShell
      title={billing ? 'Credits & billing' : 'Subscription'}
      sub={
        billing
          ? 'Credits pay for model calls on your account. Buy more at any time.'
          : `You’re running ${flavor?.productName || 'agentd'} with your own keys — nothing to bill.`
      }
    >
      {billing && (
        <>
          <div className="settings-group">
            <div className="settings-section">Balance</div>
            <div className="plan-card">
              <div className="plan-top">
                <span className="plan-name">
                  {credits ? `${credits.creditsRemaining.toLocaleString()} credits` : 'checking…'}
                </span>
                <button
                  className="btn"
                  type="button"
                  onClick={() => void refresh()}
                  title="Re-read your balance from the platform"
                >
                  <RefreshCw size={15} />
                  Refresh
                </button>
              </div>
              <div className="plan-price">
                {credits?.fundingSource === 'agent_subscription'
                  ? 'Funded by an agent subscription'
                  : 'Your platform balance'}
                {credits?.creditClass === 'promotional' ? <span> · promotional</span> : null}
                {credits?.modelTierMax ? <span> · up to the “{credits.modelTierMax}” tier</span> : null}
              </div>
              {credits?.creditsRemaining === 0 && (
                <ul className="plan-feats">
                  <li>
                    <Zap size={14} />
                    Out of credits — messages are refused until you top up.
                  </li>
                </ul>
              )}
            </div>
          </div>

          <div className="settings-group">
            <div className="settings-section">Buy credits</div>
            {catalog === null ? (
              <div className="settings-card">
                <div className="settings-row">
                  <div className="settings-label">
                    <div className="d">Loading the store…</div>
                  </div>
                </div>
              </div>
            ) : catalog.packs.length === 0 ? (
              <div className="settings-card">
                <div className="settings-row">
                  <div className="settings-label">
                    <div className="k">Nothing on sale</div>
                    <div className="d">
                      No credit packs are configured on this environment. They come from the
                      accounts service’s product catalogue.
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="cards">
                  {catalog.packs.map((p) => (
                    <div className="card" key={p.id}>
                      <div className="card-top">
                        <div className="card-icon">
                          <Sparkles size={20} />
                        </div>
                        <div>
                          <div className="card-name">{p.credits.toLocaleString()} credits</div>
                          <div className="card-by">
                            ${p.priceUsd.toFixed(2)}
                            {p.periodDays > 0 ? ` · expires after ${p.periodDays} days` : ''}
                          </div>
                        </div>
                      </div>
                      <p className="card-desc">
                        {p.title || `${p.credits.toLocaleString()} credits`}
                        {p.modelTierMax ? ` · up to the “${p.modelTierMax}” tier` : ''}
                      </p>
                      <div className="card-actions">
                        <button
                          className="btn primary"
                          type="button"
                          disabled={!!busy}
                          onClick={() => void buy(p)}
                          title={`Add ${p.credits.toLocaleString()} credits for $${p.priceUsd.toFixed(2)}`}
                        >
                          <CreditCard size={15} />
                          {busy === p.id ? 'Adding…' : `Buy · $${p.priceUsd.toFixed(2)}`}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {/* The rail's own disclosure, verbatim. Not this file's opinion. */}
                {catalog.paymentNote && <div className="settings-note">{catalog.paymentNote}</div>}
              </>
            )}
            {receipt && <div className="settings-note">{receipt}</div>}
            {error && <div className="settings-note error">{error}</div>}
          </div>
        </>
      )}

      {!billing && (
        <div className="settings-group">
          <div className="settings-section">Current plan</div>
          <div className="plan-card">
            <div className="plan-top">
              <span className="plan-name">Local</span>
              <span className="badge free">Active</span>
            </div>
            <div className="plan-price">
              Free<span> · self-hosted</span>
            </div>
            <ul className="plan-feats">
              <li>
                <Check size={14} />
                Unlimited local chats &amp; agents
              </li>
              <li>
                <Check size={14} />
                Bring your own model API keys
              </li>
              <li>
                <Check size={14} />
                Every tool &amp; plugin included
              </li>
            </ul>
          </div>
        </div>
      )}
    </PageShell>
  )
}

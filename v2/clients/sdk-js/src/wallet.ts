/**
 * Credits & billing, as a panel any agent window can mount.
 *
 *   await agentd.mountCreditsPanel({ client, mount: someElement })
 *
 * THE SAME SCREEN EVERY AGENT SHOWS, because it is the same code every agent runs. It ships inside
 * the SDK — like the sign-in gate in `gate.ts` — and `npm run build` re-vendors it into every
 * agent's `ui/vendor/agentd-client.js`. A copy under templates/ would put a second version of the
 * shop in one product, and the copy could then disagree with the accounts service it buys from.
 * That is the whole reason this is not a snippet the model writes per agent.
 *
 * LAID OUT TO MATCH the agentd renderer's Credits & billing page (SubscriptionView.tsx): balance
 * card, buy-credits grid, the rail's own disclosure, then the receipt. Same information in the
 * same order, so a user who tops up in the desktop app and then inside an agent sees one product.
 *
 * WHAT IT DOES NOT DECIDE. Not the packs (GET /products — a database row, so what is on sale
 * changes without releasing a client), not the prices (same), and not the disclosure sentence
 * (`paymentNote`, the rail's own words — so wiring up a real rail rewrites this panel's promises
 * instead of leaving a stale "no card is charged" note on a screen that now charges).
 *
 * IT RENDERS NOTHING WHEN THERE IS NOTHING TO SELL: no accounts service (a BYOK build), or nobody
 * signed in. Safe to call unconditionally, which is what makes it a component and not a decision.
 */

import { onCreditsChanged } from '@agentd/billing'
import type { Catalog, CreditPack, Credits } from '@agentd/billing'

import { authStatus } from './auth'
import { billing, type CreditsOptions } from './credits'

export interface CreditsPanelOptions extends CreditsOptions {
  /** Where to render. Defaults to `#agentd-credits` if present, else appended to <body>. */
  mount?: HTMLElement
  /** Scope the balance to one agent's subscription pocket. Defaults to the platform balance. */
  agentId?: string
  /** Where a card rail should return the customer. Defaults to this page. */
  returnUrl?: string
}

export interface CreditsPanelHandle {
  /** Re-read the balance from the server. */
  refresh(): Promise<void>
  /** Remove the panel and stop listening. */
  destroy(): void
  /** Did it render anything? False on a BYOK build or when nobody is signed in. */
  shown: boolean
}

const STYLE_ID = 'agentd-wallet-style'

// Tokens mirror the gate's (--gate-*) so an agent themes both with one palette. Every value has a
// fallback, so the panel looks deliberate in an app that never styles it.
const CSS = `
.agentd-wallet{font-family:var(--wallet-font,system-ui,-apple-system,Segoe UI,sans-serif);
  color:var(--wallet-fg,#e8eaed);display:flex;flex-direction:column;gap:18px}
.agentd-wallet[hidden]{display:none}
.agentd-wallet-h{margin:0;font-size:12px;font-weight:600;letter-spacing:.04em;
  text-transform:uppercase;color:var(--wallet-muted,#9aa0a6)}
.agentd-wallet-card{padding:16px;border-radius:var(--wallet-radius,12px);
  background:var(--wallet-card,#14171d);border:1px solid var(--wallet-border,rgba(255,255,255,.1))}
.agentd-wallet-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.agentd-wallet-bal{font-size:22px;font-weight:650}
.agentd-wallet-sub{margin-top:4px;font-size:12.5px;color:var(--wallet-muted,#9aa0a6)}
.agentd-wallet-warn{margin-top:10px;font-size:12.5px;color:var(--wallet-warn,#f0a35e)}
.agentd-wallet-warn[hidden]{display:none}
.agentd-wallet-grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
.agentd-wallet-pack{padding:14px;border-radius:var(--wallet-radius,12px);
  background:var(--wallet-card,#14171d);border:1px solid var(--wallet-border,rgba(255,255,255,.1));
  display:flex;flex-direction:column;gap:8px}
.agentd-wallet-name{font-size:15px;font-weight:620}
.agentd-wallet-meta{font-size:12.5px;color:var(--wallet-muted,#9aa0a6)}
.agentd-wallet-btn{margin-top:auto;padding:9px 12px;border:0;border-radius:8px;cursor:pointer;
  font-size:13px;font-weight:600;color:var(--wallet-on-accent,#0d1117);
  background:var(--wallet-accent,#8ab4f8)}
.agentd-wallet-btn[disabled]{opacity:.6;cursor:default}
.agentd-wallet-ghost{padding:6px 10px;border-radius:8px;cursor:pointer;font-size:12.5px;
  color:var(--wallet-fg,#e8eaed);background:transparent;
  border:1px solid var(--wallet-border,rgba(255,255,255,.16))}
.agentd-wallet-note{font-size:12.5px;line-height:1.5;color:var(--wallet-muted,#9aa0a6)}
.agentd-wallet-note[hidden]{display:none}
.agentd-wallet-err{padding:8px 10px;border-radius:8px;font-size:12.5px;
  background:var(--wallet-error-bg,rgba(163,35,43,.16));color:var(--wallet-error-fg,#f5a3a8)}
.agentd-wallet-err[hidden]{display:none}
`

function injectStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return
  const node = document.createElement('style')
  node.id = STYLE_ID
  node.textContent = CSS
  document.head.appendChild(node)
}

function el(tag: string, cls = '', text = ''): HTMLElement {
  const node = document.createElement(tag)
  if (cls) node.className = cls
  // textContent throughout: every string below is server-supplied (pack titles, the rail's note,
  // an error message) and none of it may become markup in a page it does not own.
  if (text) node.textContent = text
  return node
}

function priceLine(pack: CreditPack): string {
  const price = `$${pack.priceUsd.toFixed(2)}`
  return pack.periodDays > 0 ? `${price} · expires after ${pack.periodDays} days` : price
}

function fundingLine(c: Credits): string {
  const base =
    c.fundingSource === 'agent_subscription'
      ? 'Funded by an agent subscription'
      : 'Your platform balance'
  const promo = c.creditClass === 'promotional' ? ' · promotional' : ''
  const tier = c.modelTierMax ? ` · up to the “${c.modelTierMax}” tier` : ''
  return base + promo + tier
}

const NOTHING: CreditsPanelHandle = {
  refresh: async () => {},
  destroy: () => {},
  shown: false
}

/**
 * Mount the panel. Resolves once it has drawn, or decided not to.
 *
 * Never rejects for an ordinary refusal — a failed purchase is reported INSIDE the panel, where
 * the user can read it and try again. A daemon that cannot be reached resolves to a panel that
 * drew nothing, because the app's own status chip already reports that and a second alarm for one
 * fault is noise.
 */
export async function mountCreditsPanel(
  options: CreditsPanelOptions = {}
): Promise<CreditsPanelHandle> {
  if (typeof document === 'undefined') return NOTHING

  // Both questions before drawing anything: is there a platform at all, and is anyone signed in?
  // Showing a shop to a BYOK user implies we are billing the API key they pasted in themselves.
  let status
  try {
    status = await authStatus(options)
  } catch {
    return NOTHING
  }
  if (!status.available || !status.signedIn) return NOTHING

  injectStyle()
  const shop = billing(options)
  const agentId = options.agentId ?? ''
  const returnUrl =
    options.returnUrl || (typeof location === 'undefined' ? '' : location.href.split('#')[0])

  const balance = el('div', 'agentd-wallet-bal', 'checking…')
  const source = el('div', 'agentd-wallet-sub')
  const warn = el('div', 'agentd-wallet-warn')
  warn.hidden = true

  const refreshBtn = el('button', 'agentd-wallet-ghost', 'Refresh') as HTMLButtonElement
  refreshBtn.type = 'button'
  refreshBtn.title = 'Re-read your balance from the platform'

  const top = el('div', 'agentd-wallet-top')
  top.append(balance, refreshBtn)
  const balanceCard = el('div', 'agentd-wallet-card')
  balanceCard.append(top, source, warn)

  const grid = el('div', 'agentd-wallet-grid')
  const note = el('div', 'agentd-wallet-note')
  const receipt = el('div', 'agentd-wallet-note')
  const error = el('div', 'agentd-wallet-err')
  note.hidden = receipt.hidden = error.hidden = true

  const root = el('div', 'agentd-wallet')
  root.append(
    el('h2', 'agentd-wallet-h', 'Balance'),
    balanceCard,
    el('h2', 'agentd-wallet-h', 'Buy credits'),
    grid,
    note,
    receipt,
    error
  )

  const host =
    options.mount ||
    (document.getElementById('agentd-credits') as HTMLElement | null) ||
    document.body
  host.appendChild(root)

  function say(node: HTMLElement, text: string): void {
    node.textContent = text
    node.hidden = !text
  }

  async function refresh(): Promise<void> {
    const c = await shop.credits(agentId)
    balance.textContent = c ? `${c.creditsRemaining.toLocaleString()} credits` : 'unavailable'
    source.textContent = c ? fundingLine(c) : ''
    say(
      warn,
      c && c.creditsRemaining === 0
        ? 'Out of credits — messages are refused until you top up.'
        : ''
    )
  }

  async function buy(pack: CreditPack, btn: HTMLButtonElement): Promise<void> {
    const label = btn.textContent || ''
    btn.disabled = true
    btn.textContent = 'Adding…'
    say(error, '')
    say(receipt, '')
    try {
      const r = await shop.buy(pack.id, returnUrl)
      if (r.checkoutUrl) {
        // A card rail: the money is not ours yet and NOTHING has been granted. Send them to pay;
        // the credits arrive when the webhook does, not when this page comes back.
        say(receipt, 'Opening the payment page…')
        location.assign(r.checkoutUrl)
        return
      }
      say(
        receipt,
        r.replayed
          ? `Already bought — you have ${r.creditsRemaining.toLocaleString()} credits.`
          : `Added ${r.credits.toLocaleString()} credits. Balance: ` +
              `${r.creditsRemaining.toLocaleString()}. ${r.paymentDetail}`
      )
      // The authoritative balance, in case a message spent some mid-purchase.
      await refresh()
    } catch (e) {
      say(error, e instanceof Error ? e.message : String(e))
    } finally {
      btn.disabled = false
      btn.textContent = label
    }
  }

  function drawPacks(catalog: Catalog | null): void {
    grid.replaceChildren()
    if (!catalog) {
      grid.append(el('div', 'agentd-wallet-note', 'Could not load the store.'))
      return
    }
    if (!catalog.packs.length) {
      grid.append(
        el('div', 'agentd-wallet-note', 'No credit packs are configured on this environment.')
      )
      return
    }
    for (const pack of catalog.packs) {
      const card = el('div', 'agentd-wallet-pack')
      card.append(
        el('div', 'agentd-wallet-name', `${pack.credits.toLocaleString()} credits`),
        el('div', 'agentd-wallet-meta', priceLine(pack))
      )
      if (pack.title) card.append(el('div', 'agentd-wallet-meta', pack.title))
      const btn = el(
        'button',
        'agentd-wallet-btn',
        `Buy · $${pack.priceUsd.toFixed(2)}`
      ) as HTMLButtonElement
      btn.type = 'button'
      btn.addEventListener('click', () => void buy(pack, btn))
      card.append(btn)
      grid.append(card)
    }
    // The rail's own disclosure, verbatim. Not this file's opinion.
    say(note, catalog.paymentNote)
  }

  refreshBtn.addEventListener('click', () => void refresh())
  // A message that spends credits must move this number without the user pressing anything.
  const off = onCreditsChanged(() => void refresh())

  await refresh()
  drawPacks(await shop.catalog())

  return {
    refresh,
    shown: true,
    destroy() {
      off()
      root.remove()
    }
  }
}

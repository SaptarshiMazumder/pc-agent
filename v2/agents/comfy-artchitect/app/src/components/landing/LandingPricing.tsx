/* Pricing — the free signup credits, then the credit packs, as a sideways carousel of cards.
 *
 * EVERY BUTTON OPENS SIGN-IN. Nothing is bought without an account; after sign-in the Credits
 * page (the shared, catalogue-driven shop, paid through Razorpay) is where a pack is actually
 * purchased. The figures here mirror that catalogue — see CREDIT_PACKS for where they come from.
 *
 * WHAT A PACK BUYS, IN WORKFLOWS. A credit is meaningless to somebody deciding; "3–6 workflows"
 * is not. The ranges are rough on purpose — a workflow's cost depends on its models — and lead
 * each card's list of what it includes.
 *
 * A CAROUSEL, NOT A GRID: seven cards do not fit a row, and stacked on a phone they were seven
 * screens. It snaps card by card, with arrows and mouse drag (useSnapCarousel). No auto-advance.
 */

import { Check, ChevronLeft, ChevronRight } from 'lucide-react'

import { useSnapCarousel } from './useSnapCarousel'
import type { CREDIT_PACKS } from './landing-content'

const DOTS = 10

export function LandingPricing({
  signupCredits,
  signupWorkflows,
  packs,
  perks,
  onStart,
}: {
  signupCredits: number
  signupWorkflows: string
  packs: typeof CREDIT_PACKS
  perks: string[]
  onStart: () => void
}): JSX.Element {
  const { track, atStart, atEnd, nudge, dragHandlers } = useSnapCarousel<HTMLDivElement>()
  const max = Math.log10(Math.max(...packs.map((p) => p.credits)))
  const lit = (credits: number) => Math.max(1, Math.round((Math.log10(credits) / max) * DOTS))
  const dots = (n: number) => (
    <div className="lp-plan-dots" aria-hidden="true">
      {Array.from({ length: DOTS }, (_, i) => (
        <i key={i} className={i < n ? 'on' : ''} />
      ))}
    </div>
  )

  return (
    <div className="lp-plans-wrap">
      <div
        className={`lp-plans${atStart ? ' at-start' : ''}${atEnd ? ' at-end' : ''}`}
        ref={track}
        {...dragHandlers}
      >
        <article className="lp-plan is-free">
          <span className="lp-plan-name">Free</span>
          <div className="lp-plan-price">
            <b>₹0</b>
            <span>on signup</span>
          </div>
          <div className="lp-plan-credits">{signupCredits.toLocaleString('en-IN')} credits</div>
          {dots(lit(signupCredits))}
          <button className="lp-plan-btn is-primary" onClick={onStart}>
            Start free
          </button>
          <ul className="lp-plan-perks">
            <li className="is-yield"><Check size={13} strokeWidth={2.6} /> {signupWorkflows}</li>
            <li><Check size={13} strokeWidth={2.6} /> No card needed</li>
            <li><Check size={13} strokeWidth={2.6} /> Your first workflow is on us</li>
          </ul>
        </article>

        {packs.map((p) => (
          <article key={p.name} className={`lp-plan${p.note ? ' is-featured' : ''}`}>
            {p.note && <span className="lp-plan-flag">{p.note}</span>}
            <span className="lp-plan-name">{p.name}</span>
            <div className="lp-plan-price">
              <b>{p.inr}</b>
              <span>one-time</span>
            </div>
            <div className="lp-plan-credits">{p.credits.toLocaleString('en-IN')} credits</div>
            {dots(lit(p.credits))}
            <button className="lp-plan-btn" onClick={onStart}>
              Buy credits
            </button>
            <ul className="lp-plan-perks">
              <li className="is-yield"><Check size={13} strokeWidth={2.6} /> {p.workflows}</li>
              {perks.map((k) => (
                <li key={k}><Check size={13} strokeWidth={2.6} /> {k}</li>
              ))}
            </ul>
          </article>
        ))}
      </div>
      <button type="button" className="lp-reel-btn is-prev" onClick={() => nudge(-1)} disabled={atStart} aria-label="Cheaper packs">
        <ChevronLeft size={20} strokeWidth={2.2} />
      </button>
      <button type="button" className="lp-reel-btn is-next" onClick={() => nudge(1)} disabled={atEnd} aria-label="Bigger packs">
        <ChevronRight size={20} strokeWidth={2.2} />
      </button>
    </div>
  )
}

export default LandingPricing

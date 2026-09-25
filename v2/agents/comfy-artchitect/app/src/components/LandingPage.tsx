/* The page somebody sees before they have an account.
 *
 * WHAT IT SELLS (the creative-studio redesign, Sep 2026). Not "a ComfyUI agent" — an AI image
 * and video studio whose results come with the workflow that made them. People arrive wanting a
 * picture or a clip; the thing nobody else hands them is the reusable setup, runnable again here
 * in one click or in their own ComfyUI. The saving argued is time and effort, never price per
 * render. ComfyUI is named once, in "Under the hood", for the people who already know it.
 *
 * IT SITS IN FRONT OF THE GATE, NOT INSTEAD OF IT. `Gate` (src/common/auth/) still decides
 * whether an account is required and the daemon still refuses to run without one — a run
 * provisions a GPU that costs money. EVERY CONTROL HERE CALLS `onStart`, which opens sign-in:
 * the top bar's two buttons, the inert composer, each workflow card, and the closing CTA. The
 * in-page links (Workflows, How it works, Pricing) only scroll.
 *
 * REAL RENDERS ONLY, from public/marketing/. Every picture goes through `LandingShot`, which shows
 * a labelled frame when a file is missing rather than a broken image.
 *
 * EVERYTHING ANIMATES ON SCROLL, CHEAPLY. One IntersectionObserver adds a class; CSS does the
 * rest, and `prefers-reduced-motion` turns it all off.
 */

import {
  ArrowRight,
  Check,
  Clapperboard,
  Image as ImageIcon,
  IndianRupee,
  MessageSquareText,
  Plus,
  Repeat,
  UserRound,
} from 'lucide-react'
import { useEffect, useRef } from 'react'

import { BrandMark } from './BrandMark'
import { LandingHeroShowcase } from './landing/LandingHeroShowcase'
import { LandingHowItWorks } from './landing/LandingHowItWorks'
import { LandingRenderReel } from './landing/LandingRenderReel'
import { LandingWorkflowCard } from './landing/LandingWorkflowCard'
import { LandingModelMix } from './landing/LandingModelMix'
import { LandingPipelines } from './landing/LandingPipelines'
import { LandingStyleReuse } from './landing/LandingStyleReuse'
import { LandingCompare } from './landing/LandingCompare'
import { LandingPricing } from './landing/LandingPricing'
import {
  COMPARE_ROWS,
  CREDIT_PACKS,
  LOOP_STEPS,
  MIX_EXAMPLE,
  MODEL_TIERS,
  PACK_PERKS,
  PIPELINES,
  RENDER_REEL,
  SIGNUP_CREDITS,
  SIGNUP_WORKFLOWS,
  STARTER_WORKFLOWS,
  STYLE_RUNS,
  VALUE_POINTS,
} from './landing/landing-content'

import './landing/landing.css'

/** Adds `is-in` to anything with `data-reveal` once it has been scrolled to. */
function useReveal() {
  const root = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = root.current
    if (!el) return
    const targets = Array.from(el.querySelectorAll<HTMLElement>('[data-reveal]'))
    // NO OBSERVER, NO PROBLEM: without IntersectionObserver every section is simply shown. A
    // marketing page that stays blank because an API is missing is worse than one that does not
    // animate.
    if (typeof IntersectionObserver === 'undefined') {
      targets.forEach((t) => t.classList.add('is-in'))
      return
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return
          e.target.classList.add('is-in')
          io.unobserve(e.target) // reveal once; re-animating on scroll-back is nausea, not polish
        })
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.08 },
    )
    targets.forEach((t) => io.observe(t))
    return () => io.disconnect()
  }, [])
  return root
}

export function LandingPage({ onStart }: { onStart: () => void }): JSX.Element {
  const root = useReveal()

  /* In-page links scroll the page's own scroller (`.lp`), not the window — the shell pins
     html/body to the viewport, so a plain #hash would jump nowhere. */
  const jump = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    root.current?.querySelector(`#${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="lp" ref={root}>
      <header className="lp-top">
        <span className="lp-brand">
          <span className="lp-mark">
            <BrandMark size={20} />
          </span>
          Comfy Penguin
        </span>
        <nav className="lp-nav" aria-label="Sections">
          <a href="#lp-how" onClick={jump('lp-how')}>How it works</a>
          <a href="#lp-examples" onClick={jump('lp-examples')}>Examples</a>
          <a href="#lp-models" onClick={jump('lp-models')}>Models</a>
          <a href="#lp-pricing" onClick={jump('lp-pricing')}>Pricing</a>
        </nav>
        <span className="lp-top-right">
          <button className="lp-signin" onClick={onStart}>
            Sign in
          </button>
          <button className="lp-btn lp-btn-primary lp-btn-pill lp-btn-sm" onClick={onStart}>
            Start free
          </button>
        </span>
      </header>

      <main className="lp-main">
        {/* ── hero ─────────────────────────────────────────────────────────── */}
        <section className="lp-hero">
          <div className="lp-hero-copy">
            {/* TWO LINES, TWO JOBS: what it is, then why it is the one to pick in India. */}
            <span className="lp-india">
              <IndianRupee size={13} strokeWidth={2.4} aria-hidden="true" />
              India&rsquo;s most cost-effective AI image &amp; video tool
            </span>
            <span className="lp-eyebrow">
              <i aria-hidden="true" />
              AI agent for image &amp; video creation
            </span>
            <h1 className="lp-h1">
              Make it once.
              <br />
              <span className="lp-h1-accent">Reuse it forever.</span>
            </h1>
            <p className="lp-sub">
              Describe any image or video. Penguin makes it with the <em>best model for each
              step</em> — free or premium — and saves it as a <em>workflow</em> you can reuse
              anytime.
            </p>

            {/* THE COMPOSER, ON THE LANDING PAGE AND DELIBERATELY INERT. Showing the thing you
                will type into says what this is in one glance, and makes the first click the
                first step rather than a detour through an account form. No session, no socket —
                touching any of it opens sign-in, because the daemon would refuse an anonymous run
                anyway and faking it would only move the refusal somewhere more confusing. */}
            <div
              className="lp-composer"
              role="button"
              tabIndex={0}
              onClick={onStart}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onStart()
                }
              }}
            >
              <span className="lp-composer-ph">
                An 8-second clip of my model talking to camera in a satin bomber…
              </span>
              <div className="lp-composer-row">
                <span className="lp-composer-ico" aria-hidden="true">
                  <Plus size={16} strokeWidth={2} />
                </span>
                <span className="lp-chip"><ImageIcon size={14} strokeWidth={1.9} /> Image</span>
                <span className="lp-chip is-on"><Clapperboard size={14} strokeWidth={1.9} /> Video</span>
                <span className="lp-chip"><UserRound size={14} strokeWidth={1.9} /> Consistent character</span>
                <span className="grow" />
                <span className="lp-composer-send">
                  Start free <ArrowRight size={15} strokeWidth={2.4} />
                </span>
              </div>
            </div>

            <ul className="lp-cta-note">
              <li><Check size={13} strokeWidth={2.6} /> {SIGNUP_CREDITS.toLocaleString('en-IN')} free credits</li>
              <li><Check size={13} strokeWidth={2.6} /> Pay as you go, in ₹</li>
              <li><Check size={13} strokeWidth={2.6} /> No subscription</li>
            </ul>
          </div>
          <LandingHeroShowcase />
        </section>

        <LandingRenderReel shots={RENDER_REEL} />

        {/* ── why Penguin: the whole pitch in three cards ─────────────────────── */}
        <section className="lp-sec" id="lp-why" data-reveal>
          <div className="lp-sec-head is-center">
            <span className="lp-label">Why Penguin</span>
            <h2 className="lp-h2">Better results. A fraction of the cost.</h2>
            <p className="lp-sec-sub">Made for creators in India — no dollar subscriptions, pay only for what you make.</p>
          </div>
          <div className="lp-why">
            {VALUE_POINTS.map((v, i) => {
              const Icon = [MessageSquareText, IndianRupee, Repeat][i] || Check
              return (
                <article key={v.title} className="lp-why-card">
                  <span className="lp-why-ico" aria-hidden="true">
                    <Icon size={18} strokeWidth={2} />
                  </span>
                  <h3>{v.title}</h3>
                  <p>{v.body}</p>
                </article>
              )
            })}
          </div>
        </section>

        {/* ── how it works ─────────────────────────────────────────────────── */}
        <section className="lp-sec" id="lp-how" data-reveal>
          <div className="lp-sec-head is-center">
            <span className="lp-label">How it works</span>
            <h2 className="lp-h2">Describe. Pick. Keep.</h2>
          </div>
          <LandingHowItWorks steps={LOOP_STEPS} />
        </section>

        {/* ── real pipelines, inputs to outputs ──────────────────────────────── */}
        <section className="lp-sec" id="lp-examples" data-reveal>
          <div className="lp-sec-head">
            <span className="lp-label">Real examples</span>
            <h2 className="lp-h2">What goes in. What comes out.</h2>
          </div>
          <LandingPipelines pipelines={PIPELINES} />
        </section>

        {/* ── free + premium: the price and quality argument ──────────────────── */}
        <section className="lp-sec" id="lp-models" data-reveal>
          <div className="lp-sec-head is-center">
            <span className="lp-label">Every model</span>
            <h2 className="lp-h2">Open source or premium. Your call.</h2>
            <p className="lp-sec-sub">Choose free or paid models for any job — Penguin builds it your way.</p>
          </div>
          <LandingModelMix example={MIX_EXAMPLE} tiers={MODEL_TIERS} />
        </section>

        {/* ── reuse: one workflow, four pets ──────────────────────────────────── */}
        <section className="lp-sec" data-reveal>
          <div className="lp-sec-head">
            <span className="lp-label">Reusable workflows</span>
            <h2 className="lp-h2">Run it as many times as you need.</h2>
            <p className="lp-sec-sub">Swap the photo, change the style, run it again.</p>
          </div>
          <LandingStyleReuse workflow={STARTER_WORKFLOWS[3]} runs={STYLE_RUNS} onStart={onStart} />
        </section>

        {/* ── vs other AI video apps ──────────────────────────────────────────── */}
        <section className="lp-sec" id="lp-compare" data-reveal>
          <div className="lp-sec-head is-center">
            <span className="lp-label">Why switch</span>
            <h2 className="lp-h2">Cheaper. Simpler. Yours to keep.</h2>
          </div>
          <LandingCompare rows={COMPARE_ROWS} />
        </section>

        {/* ── starter workflows ────────────────────────────────────────────── */}
        <section className="lp-sec" id="lp-workflows" data-reveal>
          <div className="lp-sec-head">
            <span className="lp-label">Start from a workflow</span>
            <h2 className="lp-h2">Ready-made. Make them yours.</h2>
          </div>
          <div className="lp-wf-grid">
            {STARTER_WORKFLOWS.map((w) => (
              <LandingWorkflowCard key={w.title} workflow={w} action="Use this workflow" onAction={onStart} />
            ))}
          </div>
        </section>

        {/* ── under the hood ───────────────────────────────────────────────── */}
        <section className="lp-hood" data-reveal>
          <span className="lp-label">Under the hood</span>
          <div className="lp-hood-copy">
            <b>Built on open ComfyUI.</b>
            <p>Download any workflow and run it on your own GPU. No lock-in.</p>
          </div>
        </section>

        {/* ── pricing ──────────────────────────────────────────────────────────
            Every button opens sign-in: nothing is bought without an account, and the Credits page
            is where a pack is actually purchased. Figures mirror the accounts catalogue — see
            CREDIT_PACKS in landing-content.ts. */}
        <section className="lp-sec" id="lp-pricing" data-reveal>
          <div className="lp-sec-head is-center">
            <span className="lp-label">Pricing</span>
            <h2 className="lp-h2">Pay in rupees. Only for what you make.</h2>
            <p className="lp-sec-sub">
              {SIGNUP_CREDITS.toLocaleString('en-IN')} free credits on signup. No subscription — top up when you need more.
            </p>
          </div>
          <LandingPricing signupCredits={SIGNUP_CREDITS} signupWorkflows={SIGNUP_WORKFLOWS} packs={CREDIT_PACKS} perks={PACK_PERKS} onStart={onStart} />
        </section>
      </main>

      <footer className="lp-foot">
        <span className="lp-foot-brand">
          <BrandMark size={18} /> Comfy Penguin
        </span>
        <nav className="lp-foot-links">
          <a href="about.html">About</a>
          <a href="contact.html">Contact</a>
          <a href="terms.html">Terms</a>
          <a href="privacy.html">Privacy</a>
          <a href="refund.html">Refunds</a>
          <a href="delivery.html">Delivery</a>
        </nav>
      </footer>
    </div>
  )
}

export default LandingPage

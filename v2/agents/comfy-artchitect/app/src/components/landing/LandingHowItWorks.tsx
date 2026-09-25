/* How it works — three steps: describe, approve the plan, get it and keep it.
 *
 * THREE CARDS SIDE BY SIDE WHERE THERE IS ROOM; ONE SLIDE AT A TIME WHERE THERE IS NOT. Stacked on
 * a phone the three cards were three screens of scrolling for one idea, so below the breakpoint the
 * row becomes a snap carousel that advances by itself (every few seconds, pausing while touched,
 * hovered or off screen, and not at all under reduced motion) with a dot per step to jump to one.
 * The auto-advance only runs when the row actually overflows, so a wide screen never moves.
 *
 * STEP 2 IS THE REAL ASK, SIMPLIFIED. In the app the agent's plan lists each paid model with its
 * credit cost next to a free open-source alternative, and nothing runs until the person picks.
 * This card shows exactly that shape — two premium rows, one free row, one button — without the
 * brief questions and build order the real one also carries.
 */

import { Check, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { LandingShot } from './LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'
import type { LOOP_STEPS } from './landing-content'

const ADVANCE_MS = 850

function StepVisual({ index }: { index: number }) {
  if (index === 0) {
    return (
      <div className="lp-bubble">
        Put this jacket on her, then make a talking reel.
        <span className="lp-bubble-atts">
          <LandingShot src="marketing/tryon-jacket-garment.webp" alt="" className="lp-bubble-att" />
          <LandingShot src="marketing/tryon-jacket-model.webp" alt="" className="lp-bubble-att" />
        </span>
      </div>
    )
  }
  if (index === 1) {
    return (
      <div className="lp-mini-plan">
        <b className="lp-mini-plan-title">3 jacket posts + a talking reel</b>
        <label className="lp-mini-opt is-on">
          <span className="lp-mini-check"><Check size={11} strokeWidth={3} /></span>
          <span className="lp-mini-opt-text"><b>Nano Banana 2</b><small>the 3 photos</small></span>
          <span className="lp-tier is-premium"><Sparkles size={11} strokeWidth={2.2} /> 54K cr</span>
        </label>
        <label className="lp-mini-opt is-on">
          <span className="lp-mini-check"><Check size={11} strokeWidth={3} /></span>
          <span className="lp-mini-opt-text"><b>Kling 3.0</b><small>the talking reel</small></span>
          <span className="lp-tier is-premium"><Sparkles size={11} strokeWidth={2.2} /> 154K cr</span>
        </label>
        <label className="lp-mini-opt">
          <span className="lp-mini-check" />
          <span className="lp-mini-opt-text"><b>Qwen Image Edit</b><small>free alternative</small></span>
          <span className="lp-tier is-open"><Check size={11} strokeWidth={2.6} /> Free</span>
        </label>
        <span className="lp-btn lp-btn-primary lp-btn-sm lp-mini-plan-go">Build it</span>
      </div>
    )
  }
  return (
    <div className="lp-loop-grid">
      <LandingShot src="marketing/tryon-jacket-post.webp" alt="" className="shot-fill" />
      <LandingShot src="marketing/tryon-jacket-reel-poster.jpg" alt="" className="shot-fill" />
      <span className="lp-loop-keep">
        <MediaKindTag kind="workflow" />
        <b>Saved · Run again</b>
      </span>
    </div>
  )
}

export function LandingHowItWorks({ steps }: { steps: typeof LOOP_STEPS }): JSX.Element {
  const track = useRef<HTMLDivElement | null>(null)
  const [active, setActive] = useState(0)
  const paused = useRef(false)
  const visible = useRef(false)

  const go = useCallback((i: number) => {
    const el = track.current
    const first = el?.children[0] as HTMLElement | undefined
    const card = el?.children[i] as HTMLElement | undefined
    if (el && first && card) el.scrollTo({ left: card.offsetLeft - first.offsetLeft, behavior: 'smooth' })
  }, [])

  // Which step is showing: each card is one track-width (plus the gap), so it is a division.
  useEffect(() => {
    const el = track.current
    if (!el) return
    const onScroll = () => {
      const first = el.children[0] as HTMLElement | undefined
      const second = el.children[1] as HTMLElement | undefined
      const pitch = first && second ? second.offsetLeft - first.offsetLeft : el.clientWidth
      setActive(Math.max(0, Math.min(steps.length - 1, Math.round(el.scrollLeft / Math.max(1, pitch)))))
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [steps.length])

  // Only advance while on screen — a carousel nobody is looking at has no reason to move.
  useEffect(() => {
    const el = track.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([e]) => (visible.current = e.isIntersecting), { threshold: 0.4 })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const t = window.setInterval(() => {
      const el = track.current
      // Nothing to advance where all three fit side by side.
      if (!el || paused.current || !visible.current || el.scrollWidth <= el.clientWidth + 2) return
      go((active + 1) % steps.length)
    }, ADVANCE_MS)
    return () => window.clearInterval(t)
  }, [active, go, steps.length])

  const hold = () => (paused.current = true)
  const release = () => (paused.current = false)

  return (
    <div className="lp-how">
      <div
        className="lp-loop"
        ref={track}
        onPointerEnter={hold}
        onPointerLeave={release}
        onTouchStart={hold}
        onTouchEnd={release}
      >
        {steps.map((s, i) => (
          <article key={s.n} className="lp-loop-card">
            <span className="lp-loop-n">Step {i + 1}</span>
            <h3 className="lp-loop-t">{s.title}</h3>
            <p className="lp-loop-b">{s.body}</p>
            <div className="lp-loop-vis">
              <StepVisual index={i} />
            </div>
          </article>
        ))}
      </div>
      <div className="lp-dots" role="tablist" aria-label="Steps">
        {steps.map((s, i) => (
          <button
            key={s.n}
            type="button"
            role="tab"
            aria-selected={active === i}
            aria-label={`Step ${i + 1}: ${s.title}`}
            className={`lp-dot${active === i ? ' on' : ''}`}
            onClick={() => go(i)}
          />
        ))}
      </div>
    </div>
  )
}

export default LandingHowItWorks

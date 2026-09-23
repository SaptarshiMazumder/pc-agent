/* The page somebody sees before they have an account.
 *
 * WHAT THIS REPLACED: the sign-in card, as the first thing on the domain. A visitor arrived at
 * comfypenguin.com and was asked for a Google account before anything had told them what the
 * product was or what it cost — the highest-friction opening available, and it loses everybody
 * who was merely curious, which before launch is everybody.
 *
 * IT SITS IN FRONT OF THE GATE, NOT INSTEAD OF IT. `Gate` (src/common/auth/) still decides
 * whether an account is required and the daemon still refuses to run without one — a run
 * provisions a GPU that costs money. Nothing here weakens that. The ask simply arrives after
 * the pitch instead of before it.
 *
 * NO PHOTOGRAPHS, AND THAT IS A CONSTRAINT RATHER THAN A STYLE. There are no shippable renders
 * in this repo; the only images are e2e reference photos of people, which are test fixtures.
 * So the page is carried by a drawn ComfyUI graph (WorkflowDiagram) — which for a product that
 * BUILDS workflows says more than a photograph would — plus typography and gradient. Every
 * place a real render belongs is a `LandingShot`, which shows a labelled frame until a file
 * exists at its path and the render itself the moment one does.
 *
 * EVERYTHING ANIMATES ON SCROLL, CHEAPLY. One IntersectionObserver adds a class; CSS does the
 * rest, and `prefers-reduced-motion` turns it all off. No animation library on a page whose
 * entire job is to appear instantly.
 */

import { ArrowUp, Check, Plus } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { BrandMark } from './BrandMark'
import { UseCaseCarousel } from './landing/UseCaseCarousel'
import { WorkflowDiagram } from './landing/WorkflowDiagram'

import './landing/landing.css'

/** What it drives. Named rather than logo'd: we ship no marks we have licence to. */
const DRIVES = [
  'ComfyUI',
  'FLUX',
  'SDXL',
  'WAN 2.2',
  'Qwen-Image',
  'LTX-Video',
  'ControlNet',
  'IP-Adapter',
  'AnimateDiff',
  'Vast.ai',
  'RunPod',
  'Modal',
]

/** The shelf of things to make. Each has a render slot for when there is one to show. */
const USE_CASES: { title: string; body: string; shot: string }[] = [
  {
    title: 'Realistic AI influencer',
    body: 'One face, held consistent across a whole shoot — poses, outfits, lighting.',
    shot: 'marketing/influencer.webp',
  },
  {
    title: 'Product ad video',
    body: 'A still of your product becomes a moving shot with camera motion and a look.',
    shot: 'marketing/product-ad.mp4',
  },
  {
    title: 'Same person, new angles',
    body: 'Give it one reference photo. Get the same subject from angles you never shot.',
    shot: 'marketing/angles.webp',
  },
  {
    title: 'Animate a still',
    body: 'Image-to-video, with the motion described in words rather than keyframed.',
    shot: 'marketing/animate.webp',
  },
  {
    title: 'Style transfer at scale',
    body: 'One look, applied across a batch, with the graph reused rather than rebuilt.',
    shot: 'marketing/style.webp',
  },
  {
    title: 'Upscale and restore',
    body: 'Detail recovered and resolution raised, with the nodes your instance actually has.',
    shot: 'marketing/upscale.webp',
  },
]

const FEATURES: { title: string; body: string }[] = [
  {
    title: 'It reads your instance first',
    body: 'Never a model or node from memory. It asks the server what is installed and builds against that, which is why the graphs it writes actually run.',
  },
  {
    title: 'It repairs what the server rejects',
    body: 'A red node is not the end of the job. It reads the error, fixes the wiring or the parameter, and runs again until the result is right.',
  },
  {
    title: 'Reusable pipelines, not one-offs',
    body: 'Every run hands back an importable .json workflow. Change the prompt, keep the pipeline — or hand it to somebody else and they get your setup exactly.',
  },
  {
    title: 'It installs what is missing',
    body: 'Missing custom node or checkpoint? It fetches and installs it through ComfyUI-Manager rather than sending you a shopping list.',
  },
  {
    title: 'The GPU is rented by the minute',
    body: 'No card in your machine, no CUDA afternoon. A box is provisioned for the job and shut down after, and you are charged for the work, not the idling.',
  },
  {
    title: 'Or point it at your own box',
    body: 'Already running ComfyUI? Give it the URL. It reads your models, builds against them, and the workflow files are yours either way.',
  },
]

/* The four beats of the ad, in the same order and the same words. A landing page that argues
   differently from the thing that brought somebody here makes them wonder if they clicked the
   right link. */
const STEPS: { n: string; title: string; body: string }[] = [
  { n: '01', title: 'Describe it', body: 'Tell it what you want. In plain English.' },
  { n: '02', title: 'It creates the workflow', body: 'Picks the models. Wires the nodes.' },
  { n: '03', title: 'It tests and runs it', body: 'On our cloud. It fixes what the server rejects.' },
  { n: '04', title: 'You keep it', body: 'The images, the video, and the workflow file itself.' },
]

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

  return (
    <div className="lp" ref={root}>
      <header className="lp-top">
        <span className="lp-brand">
          <span className="lp-mark">
            <BrandMark size={22} />
          </span>
          Comfy Penguin
        </span>
        <span className="lp-top-right">
          <span className="lp-free">10,000 free credits</span>
          <button className="lp-signin" onClick={onStart}>
            Sign in
          </button>
        </span>
      </header>

      <main className="lp-main">
        {/* ── hero ─────────────────────────────────────────────────────────── */}
        <section className="lp-hero">
          <div className="lp-aurora" aria-hidden="true" />
          <h1 className="lp-h1">
            <span className="lp-h1-accent">Want AI images</span>
            <br />
            but ComfyUI scares you?
          </h1>
          <p className="lp-kicker">Too many nodes. Too many models. Too many missing files.</p>
          <p className="lp-sub">
            Describe what you want. Comfy Penguin <em>creates, tests and runs</em> the workflow on
            our cloud — and you keep it.
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
              Make a 6-second product shot of this bottle, cinematic, shallow depth of field…
            </span>
            <div className="lp-composer-row">
              <span className="lp-composer-ico" aria-hidden="true">
                <Plus size={18} strokeWidth={1.9} />
              </span>
              <span className="lp-chip">Image</span>
              <span className="lp-chip">Video</span>
              <span className="lp-chip">Upscale</span>
              <span className="grow" />
              <span className="lp-composer-send">
                Start free <ArrowUp size={15} strokeWidth={2.4} />
              </span>
            </div>
          </div>

          <p className="lp-cta-note">
            10,000 credits free · no card to start · packs from $1 after that
          </p>
          <p className="lp-punch">Don&rsquo;t be a chicken. Be a penguin.</p>
        </section>

        {/* ── what it drives ───────────────────────────────────────────────── */}
        <section className="lp-marquee" aria-label="Works with">
          <div className="lp-marquee-track">
            {/* Twice, so the loop has no seam. The copy is hidden from screen readers. */}
            {[0, 1].map((pass) => (
              <span key={pass} className="lp-marquee-run" aria-hidden={pass === 1}>
                {DRIVES.map((d) => (
                  <span key={d} className="lp-marquee-item">
                    {d}
                  </span>
                ))}
              </span>
            ))}
          </div>
        </section>

        {/* ── the graph ────────────────────────────────────────────────────── */}
        <section className="lp-sec lp-graph" data-reveal>
          <div className="lp-sec-head">
            <h2 className="lp-h2">It creates the workflow.</h2>
            <p className="lp-sec-sub">
              Picks the models. Wires the nodes. Runs it, reads what the server rejects, and
              fixes it — then hands you the graph as a file you can import.
            </p>
          </div>
          <div className="lp-graph-box">
            <WorkflowDiagram />
          </div>
          <ul className="lp-ticks">
            {[
              'Never names a model it has not seen on the box',
              'Re-runs and repairs until the graph completes',
              'Hands back an importable .json and an installer',
            ].map((t) => (
              <li key={t}>
                <Check size={15} strokeWidth={2.4} /> {t}
              </li>
            ))}
          </ul>
        </section>

        {/* ── use cases ────────────────────────────────────────────────────── */}
        <section className="lp-sec" data-reveal>
          <div className="lp-sec-head">
            <h2 className="lp-h2">What people build with it</h2>
            <p className="lp-sec-sub">
              Every one of these is a pipeline you keep — not a one-off render. Change the
              prompt, reuse the graph.
            </p>
          </div>
          <UseCaseCarousel items={USE_CASES} />
        </section>

        {/* ── how ──────────────────────────────────────────────────────────── */}
        <section className="lp-sec" data-reveal>
          <div className="lp-sec-head">
            <h2 className="lp-h2">Describe it. Let the agent build it.</h2>
          </div>
          <div className="lp-steps">
            {STEPS.map((s) => (
              <article key={s.n} className="lp-step">
                <span className="lp-step-n">{s.n}</span>
                <h3 className="lp-step-t">{s.title}</h3>
                <p className="lp-step-b">{s.body}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ── features ─────────────────────────────────────────────────────── */}
        <section className="lp-sec" data-reveal>
          <div className="lp-sec-head">
            <h2 className="lp-h2">Why its workflows actually run</h2>
            <p className="lp-sec-sub">
              Most tools emit JSON and stop. Everything after that is the difference.
            </p>
          </div>
          <div className="lp-feats">
            {FEATURES.map((f, i) => (
              <article key={f.title} className="lp-feat" data-reveal style={{ '--d': `${i * 50}ms` } as React.CSSProperties}>
                <h3 className="lp-feat-t">{f.title}</h3>
                <p className="lp-feat-b">{f.body}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ── close ────────────────────────────────────────────────────────── */}
        <section className="lp-sec lp-close" data-reveal>
          <h2 className="lp-h2">Meet Comfy Penguin.</h2>
          <p className="lp-sec-sub">
            An agent trained to create, test and run any ComfyUI workflow for you. Start with
            10,000 free credits — no card, packs from $1 when you want more.
          </p>
          <button className="lp-cta" onClick={onStart}>
            Get started
          </button>
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

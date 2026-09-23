/* The page somebody sees before they have an account.
 *
 * WHAT THIS REPLACED: the sign-in card, as the first thing on the domain. A visitor arrived at
 * comfypenguin.com and was asked to hand over a Google account before anything had told them
 * what the product was or what it cost. That is the highest-friction possible opening, and it
 * loses the people who were only curious -- which, before launch, is everybody.
 *
 * IT SITS IN FRONT OF THE GATE, NOT INSTEAD OF IT. `Gate` (src/common/auth/) still decides
 * whether an account is required, and the daemon still refuses to run anything without one --
 * that constraint is real, because a run provisions a GPU that costs money. Nothing here
 * weakens it. The only change is that the ask now arrives AFTER the pitch instead of before it.
 *
 * A RETURNING USER NEVER SEES THIS. main.tsx asks `authStatus()` first and mounts the app
 * directly for anyone already signed in; this renders only for a genuine stranger. An extra
 * click for someone who visits daily would be a worse trade than the wall it replaces.
 *
 * NO IMAGES. Everything here is type and CSS, because the one thing this page must do is appear
 * instantly -- a landing page that waits on a hero render has already lost the visitor it was
 * built for.
 */

import { ArrowUp, Plus } from 'lucide-react'

import { BrandMark } from './BrandMark'

/** What the agent actually does, in the order a stranger needs it. */
const STEPS: { n: string; title: string; body: string }[] = [
  {
    n: '01',
    title: 'Tell it what you want',
    body: 'Plain words. "A realistic influencer photo", "animate this still", "the same person from three new angles."',
  },
  {
    n: '02',
    title: 'It rents the GPU and sets ComfyUI up',
    body: 'No install, no nodes to wire, no models to hunt down. The machine is provisioned for the job and shut down after.',
  },
  {
    n: '03',
    title: 'It builds, runs and repairs the graph',
    body: 'It reads what the server actually says, fixes what it rejects, and keeps going until the result is right.',
  },
  {
    n: '04',
    title: 'You keep the workflow, not just the image',
    body: 'Every run hands back an importable ComfyUI workflow file and an installer, so you can reproduce it on your own machine.',
  },
]

/** The four the composer already offers — same words, so the promise and the product agree. */
const EXAMPLES = [
  'Realistic AI influencer',
  'Product ad video',
  'Same person, new angles',
  'Animate a still',
]

export function LandingPage({ onStart }: { onStart: () => void }): JSX.Element {
  return (
    <div className="lp">
      <header className="lp-top">
        <span className="lp-brand">
          <span className="lp-mark">
            <BrandMark size={22} />
          </span>
          Comfy Penguin
        </span>
        {/* The same door as the button below. Somebody who already has an account is not here to
            read the pitch, and making them scroll for the way in is its own small insult. */}
        <button className="lp-signin" onClick={onStart}>
          Sign in
        </button>
      </header>

      <main className="lp-main">
        <section className="lp-hero">
          <h1 className="lp-h1">
            ComfyUI workflows,
            <br />
            without the wiring.
          </h1>
          <p className="lp-sub">
            Describe what you want. Comfy Penguin rents a GPU, sets ComfyUI up on it, builds the
            graph, runs it, and repairs whatever the server rejects — until the result is right.
            You get the images <em>and</em> the workflow file.
          </p>
          <button className="lp-cta" onClick={onStart}>
            Get started
          </button>
          <p className="lp-cta-note">
            Google sign-in. Credits from $1 — you only pay for the model calls you use.
          </p>
        </section>

        {/* THE COMPOSER, ON THE LANDING PAGE AND NOT YET REAL.
            Showing the thing you will actually type into does more than any paragraph: it says
            what this is in one glance, and the first click is already the first step rather
            than a detour through an account form.

            IT IS DELIBERATELY INERT. A readonly input and a button, no session, no socket --
            touching any of it opens the sign-in card instead. The daemon would refuse an
            anonymous run anyway (a run provisions a GPU that costs money), so pretending
            otherwise would only move the refusal somewhere more confusing.

            role="button" and a real <button> for the send: this has to be reachable by keyboard
            and announced as something that does something, not as a text field that silently
            does nothing. */}
        <section className="lp-try" aria-label="Try it">
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
            <span className="lp-composer-ph">What should we build?</span>
            <div className="lp-composer-row">
              <span className="lp-composer-ico" aria-hidden="true">
                <Plus size={18} strokeWidth={1.9} />
              </span>
              {EXAMPLES.map((e) => (
                <span key={e} className="lp-chip">
                  {e}
                </span>
              ))}
              <span className="grow" />
              <span className="lp-composer-send" aria-hidden="true">
                <ArrowUp size={17} strokeWidth={2.2} />
              </span>
            </div>
          </div>
        </section>

        <section className="lp-steps">
          {STEPS.map((s) => (
            <article key={s.n} className="lp-step">
              <span className="lp-step-n">{s.n}</span>
              <h2 className="lp-step-t">{s.title}</h2>
              <p className="lp-step-b">{s.body}</p>
            </article>
          ))}
        </section>

        {/* THE HONEST PARAGRAPH. Somebody who already owns a 4090 should know this runs on a
            rented machine and costs credits, before they sign up and find out. A landing page
            that only sells makes the first real interaction a correction. */}
        <section className="lp-note">
          <p>
            <strong>Already run ComfyUI yourself?</strong> Point it at your own instance instead —
            it reads what you have installed and builds against that, and the workflow files it
            writes are yours to keep either way.
          </p>
        </section>
      </main>

      <footer className="lp-foot">
        <a href="about.html">About</a>
        <a href="contact.html">Contact</a>
        <a href="terms.html">Terms</a>
        <a href="privacy.html">Privacy</a>
        <a href="refund.html">Refunds</a>
        <a href="delivery.html">Delivery</a>
      </footer>
    </div>
  )
}

export default LandingPage

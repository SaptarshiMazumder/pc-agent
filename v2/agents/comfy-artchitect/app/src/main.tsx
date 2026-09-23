/* The entry point.
 *
 * THREE STATES, IN THIS ORDER: deciding, the landing page, the app.
 *
 * SIGN-IN IS A COMPONENT, NOT A STEP. `<Gate>` asks the daemon whether an account is REQUIRED
 * and shows the sign-in card if one is and nobody is signed in; otherwise it renders straight
 * through. This used to be an async IIFE that awaited `signInFirst()` before rendering anything,
 * because the gate was a vanilla-DOM panel that painted itself over the page and had to run
 * first. It is agentd's React card now, so it lives inside the tree like everything else.
 *
 * WHY A LANDING PAGE IN FRONT OF IT. On a hosted deployment the gate DEMANDS an account, so the
 * first thing a stranger met on comfypenguin.com was a Google button and nothing else -- no
 * statement of what the product is, what it costs, or why an account is wanted. That is the
 * highest-friction opening available and it loses everybody who was merely curious.
 *
 * THE GATE IS NOT WEAKENED. It still decides, the daemon still refuses to run without an
 * account, and a run still provisions a GPU that costs real money. The ask simply arrives after
 * the pitch rather than before it.
 *
 * A KNOWN USER NEVER SEES THE LANDING. `authStatus()` is asked once, up front -- the same call
 * Gate makes -- and anyone already signed in goes straight into the app. An extra click for
 * somebody who visits every day would be a worse trade than the wall it replaced.
 *
 * AND NOTHING FLASHES WHILE WE ASK. `null` renders an empty surface rather than the landing,
 * because showing a signed-in user a marketing page for 200ms and then yanking it is worse than
 * a beat of nothing.
 *
 * THE STYLE ORDER IS LOAD-BEARING: tokens (complete defaults) -> theme (this template's own
 * decisions, overriding only what it changes) -> styles (structure, which reads both and decides
 * nothing). Move `styles.css` above `theme.css` and every template's look dies quietly.
 */

import { authStatus, oauthCallbackParams } from '@agentd/client'
import { X } from 'lucide-react'
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { BrandMark } from './components/BrandMark'
import { LandingPage } from './components/LandingPage'
import Gate from './common/auth/Gate'
import SignIn from './common/auth/SignIn'
import './tokens.css'
import './theme.css'
import './styles.css'

function Root() {
  /** null = still asking. Never render the landing while this is unknown. */
  const [knownUser, setKnownUser] = useState<boolean | null>(null)
  /** Set when the visitor asks for the door, which is what mounts the card.
   *
   *  OPEN FROM THE START ON THE WAY BACK FROM THE PROVIDER. A load carrying `?code=&state=` is
   *  a sign-in finishing, and the card is the only thing that redeems the code. Starting closed
   *  meant the probe above found no cookie (nothing had redeemed the code yet), the landing came
   *  up with no card, and the code sat in the address bar until the person pressed Start a
   *  second time — which looked like having to sign in twice. Gate already treats such a load
   *  as its card's first frame; this is the same rule on the landing's path. */
  const [entering, setEntering] = useState(() => !!oauthCallbackParams())

  /* NO WAY OUT WHILE THE CODE IS BEING SWAPPED FOR A SESSION.
     A load carrying `?code=` is a sign-in mid-flight: the card hands that one-time code to the
     server and gets a session back. SignIn clears the code from the address bar BEFORE it makes
     that call, so closing the card in those seconds leaves the exchange unfinished AND the code
     already spent — the visitor lands back on the landing page signed out, with nothing to
     retry but the whole Google round trip. The same "sign in twice", through a narrower door.

     RELEASED ON A TIMER, because the other failure is worse. If the exchange errors, SignIn
     shows its message — and a card with no close button would trap the person on it. Twenty
     seconds is far longer than a redemption and far shorter than a person's patience. */
  const [redeeming, setRedeeming] = useState(() => !!oauthCallbackParams())
  useEffect(() => {
    if (!redeeming) return
    const t = window.setTimeout(() => setRedeeming(false), 20_000)
    return () => window.clearTimeout(t)
  }, [redeeming])

  useEffect(() => {
    let live = true
    void authStatus()
      .then((s) => live && setKnownUser(Boolean(s?.signedIn)))
      .catch(() => {
        // THE DAEMON DID NOT ANSWER. Treat it as "not signed in" and show the landing: it is the
        // page that needs nothing from the server, so a stranger still sees the product while
        // the app reports its own connection trouble once they go in. Failing to the sign-in
        // card instead would explain an outage as a login problem.
        if (live) setKnownUser(false)
      })
    return () => {
      live = false
    }
  }, [])

  // Escape closes the card, like every other dialog. Bound only while it is open.
  useEffect(() => {
    if (!entering) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !redeeming) setEntering(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [entering, redeeming])

  if (knownUser === null) return null

  if (knownUser) {
    return (
      <Gate product="Comfy Penguin" mark={<BrandMark size={42} />}>
        <App />
      </Gate>
    )
  }

  /* THE CARD ARRIVES OVER THE PAGE, NOT INSTEAD OF IT.
     Replacing the landing with a full-screen sign-in throws away the thing that just persuaded
     them, and leaves no way back if they only wanted a look. Over it, with a scrim and a close
     button, the decision stays reversible.

     `SignIn` DIRECTLY RATHER THAN `Gate` HERE. Gate's job is deciding whether an account is
     demanded, and it answers that by rendering its children once satisfied -- so wrapping it
     round a modal would put the whole app inside the modal after sign-in. The card itself is
     the piece that belongs here; `onDone` flips the state that mounts the real app, and Gate
     takes over from there exactly as before. It is the same shared component either way, which
     is what validate_agent asks for. */
  return (
    <>
      <LandingPage onStart={() => setEntering(true)} />
      {entering && (
        <div className="lp-auth" role="dialog" aria-modal="true" aria-label="Sign in">
          {!redeeming && (
            <button
              className="lp-auth-x"
              aria-label="Close sign in"
              onClick={() => setEntering(false)}
            >
              <X size={18} strokeWidth={2} />
            </button>
          )}
          <SignIn
            product="Comfy Penguin"
            mark={<BrandMark size={42} />}
            onDone={() => {
              setRedeeming(false)
              setKnownUser(true)
            }}
          />
        </div>
      )}
    </>
  )
}

const host = document.getElementById('root')
// Not a fallback — a hard stop. A missing mount point means index.html and this file disagree, and
// a page that silently renders nothing is the hardest kind of build error to find.
if (!host) throw new Error('#root is missing from index.html')

createRoot(host).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)

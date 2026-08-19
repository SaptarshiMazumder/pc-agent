/* Sign in FIRST, then render. THE ONE SOURCE FILE THIS STARTER SHIPS.
 *
 * Everything else about the window is a judgement about your agent, which is why `src/` is
 * otherwise yours to write. This file is not a judgement: every agent with a window signs its
 * user in, so it arrives already done rather than as a rule to remember.
 *
 * WHY BLOCKING. An app that renders its composer first and signs in later has to answer "signed
 * in yet?" at every send site, and gets it wrong at one of them. Past this line somebody is
 * signed in — or this daemon has no accounts service, in which case the gate renders nothing.
 *
 * WHY THE SDK'S GATE AND NEVER YOUR OWN FORM. Its element ids (`gateEmail`, `gatePass`,
 * `gateForm`) are a contract the desktop shell's end-to-end login test drives, so a hand-written
 * login silently disables that test. It is also the same endpoints as the rest of the system —
 * a second login is a second way to get credentials wrong, written once by somebody who was not
 * thinking about credentials that day. Theme it with the `--gate-*` CSS custom properties.
 */

import { mountSignInGate } from '@agentd/client'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

const root = createRoot(document.getElementById('root')!)

void (async () => {
  try {
    await mountSignInGate({
      // One line saying why THIS agent wants an account. Shown under the heading, which comes
      // from the page <title> — so the product is never named twice.
      blurb: 'Sign in to continue.',
    })
  } catch (e) {
    // The accounts service being unreachable must not leave a blank window. Render the app —
    // anything that does not need an account still works, and the settings page reports the
    // account state rather than the user staring at nothing.
    console.error('sign-in gate failed', e)
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})()

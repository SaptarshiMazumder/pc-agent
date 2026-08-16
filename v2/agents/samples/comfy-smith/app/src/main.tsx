/* Sign in FIRST, then render.
 *
 * `mountSignInGate` is a blocking await on purpose: an app that renders its composer and signs in
 * later has to answer "signed in yet?" at every send site, and gets it wrong at one of them.
 * Past this line somebody is signed in — or this daemon has no accounts service, in which case
 * the gate renders nothing and Settings says so rather than leaving a blank where a login was.
 *
 * It is the SDK's, not hand-written. Its element ids are a contract the desktop shell's
 * end-to-end login hook drives, so a packaged build can be tested with nobody at the keyboard;
 * a bespoke form would silently disable that. Theming is CSS custom properties (`--gate-*`),
 * set in styles.css, so it looks like this agent without forking the markup.
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
      blurb: 'Sign in to build and run ComfyUI workflows on your server.',
    })
  } catch (e) {
    // The accounts service being unreachable must not leave a blank window. Render the app —
    // Settings reports the account state, and everything that does not need an account works.
    console.error('sign-in gate failed', e)
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})()

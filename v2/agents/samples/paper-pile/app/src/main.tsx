/* Sign in FIRST, then render.
 *
 * THROUGH THE COMMON MODULE (`src/common/auth/SignIn.tsx`), not by reaching for the SDK here. That
 * folder is copied verbatim into every agent and `validate_agent` compares it against the source,
 * so accounts and money work the same way everywhere — and this file stays about THIS agent's
 * boot rather than about how signing in works.
 *
 * Blocking on purpose: an app that renders its composer and signs in later has to answer "signed
 * in yet?" at every send site, and gets it wrong at one of them. Past this line somebody is signed
 * in — or this build has no accounts service, in which case nothing was ever asked.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { signInFirst } from './common/auth/SignIn'
import './styles.css'

const root = createRoot(document.getElementById('root')!)

void (async () => {
  // Never throws: an unreachable accounts service leaves you with an app, not a blank window.
  await signInFirst()
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})()

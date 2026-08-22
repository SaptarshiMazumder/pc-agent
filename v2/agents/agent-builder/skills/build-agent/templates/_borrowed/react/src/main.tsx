/* Sign in FIRST, then render. THE ONE SOURCE FILE THIS STARTER SHIPS.
 *
 * Everything else about the window is a judgement about your agent, which is why `src/` is
 * otherwise yours to write. This file is not a judgement: every agent with a window signs its
 * user in, so it arrives already done rather than as a rule to remember.
 *
 * WHY BLOCKING. An app that renders its composer first and signs in later has to answer "signed
 * in yet?" at every send site, and gets it wrong at one of them. Past this line somebody is
 * signed in — or this build has no accounts service, in which case nothing was ever asked.
 *
 * THE MECHANISM IS IN `common/`, NOT HERE. `src/common/` arrives with every scaffold and holds
 * accounts and money — copied verbatim, shared by every agent, and compared against its source by
 * `validate_agent`. Read `src/common/README.md`: it says what is in there and what you still have
 * to wire up (this file is one of the two things, and the Credits page is the other).
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { signInFirst } from './common/auth/SignIn'
import './styles.css'

const root = createRoot(document.getElementById('root')!)

void (async () => {
  // Renders nothing on a build with no accounts service, or when a stored session still works —
  // and never throws, so an unreachable service leaves you with an app rather than a blank window.
  await signInFirst()
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})()

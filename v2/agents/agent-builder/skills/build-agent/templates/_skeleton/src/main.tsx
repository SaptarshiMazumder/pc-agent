/* The entry point.
 *
 * SIGN-IN IS A COMPONENT, NOT A STEP. `<Gate>` asks the daemon whether an account is REQUIRED and
 * shows the sign-in card if one is and nobody is signed in; otherwise it renders straight through.
 *
 * This used to be an async IIFE that awaited `signInFirst()` before rendering anything, because the
 * gate was a vanilla-DOM panel that painted itself over the page and had to run first. It is
 * agentd's React card now, so it lives inside the tree like everything else and the window paints
 * immediately — a blank screen while a status probe runs is indistinguishable from a broken app.
 *
 * THE STYLE ORDER IS LOAD-BEARING: tokens (complete defaults) -> theme (this template's own
 * decisions, overriding only what it changes) -> styles (structure, which reads both and decides
 * nothing). Move `styles.css` above `theme.css` and every template's look dies quietly.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import Gate from './common/auth/Gate'
import './tokens.css'
import './theme.css'
import './styles.css'

const host = document.getElementById('root')
// Not a fallback — a hard stop. A missing mount point means index.html and this file disagree, and
// a page that silently renders nothing is the hardest kind of build error to find.
if (!host) throw new Error('#root is missing from index.html')

createRoot(host).render(
  <StrictMode>
    <Gate>
      <App />
    </Gate>
  </StrictMode>,
)

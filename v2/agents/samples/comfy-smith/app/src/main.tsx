/* The entry point.
 *
 * SIGN-IN IS A COMPONENT, NOT A STEP. `<Gate>` asks the daemon whether an account is REQUIRED and
 * shows the sign-in card if one is and nobody is signed in; otherwise it renders straight through.
 *
 * THROUGH THE COMMON MODULE (`src/common/auth/`), not by reaching for the SDK here. That folder is
 * copied verbatim into every agent and `validate_agent` compares it against the source, so accounts
 * and money work the same way everywhere — and this file stays about THIS agent's boot rather than
 * about how signing in works.
 *
 * This used to be an async IIFE that awaited `signInFirst()` before rendering anything, because the
 * gate was a vanilla-DOM panel that painted itself over the page and had to run first. It is the
 * assistant's React card now, so it lives inside the tree like everything else and the window
 * paints immediately — a blank screen while a status probe runs is indistinguishable from a broken
 * app.
 *
 * THE PALETTE FIRST. `tokens.css` defines the names every shared module reads; `styles.css` comes
 * after so anything this agent redefines there wins.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Gate from './common/auth/Gate'
import './tokens.css'
import './styles.css'

const host = document.getElementById('root')
// Not a fallback — a hard stop. A missing mount point means index.html and this file disagree, and
// a page that silently renders nothing is the hardest kind of build error to find.
if (!host) throw new Error('#root is missing from index.html')

createRoot(host).render(
  <StrictMode>
    <Gate product="Comfy Smith">
      <App />
    </Gate>
  </StrictMode>,
)

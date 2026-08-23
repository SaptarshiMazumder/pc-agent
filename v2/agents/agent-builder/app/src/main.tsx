import { createRoot } from 'react-dom/client'

/* THE DESIGN FONTS, VENDORED — the same three families agentd bundles, at the same weights.
 *
 * They are not optional decoration. styles.css asks for 'Hanken Grotesk' and 'Bricolage
 * Grotesque' because that is what agentd's type is set in; without these imports the stack falls
 * silently through to Segoe UI and the two windows are set in different faces while the CSS
 * insists they are not. A missing font does not error — it just quietly looks wrong, which is why
 * this went unnoticed when the token layer was ported.
 *
 * Bundled rather than linked: an agent's window is served from the daemon under a strict CSP that
 * forbids remote fonts, and a local-first product should not need the network to draw itself. */
/* LATIN AND LATIN-EXT ONLY. The catch-all `600.css` entrypoints pull every subset a family
   ships — vietnamese, cyrillic, greek — and each one lands in the built output as a file the
   repo then carries forever. They cost nothing at RUNTIME (unicode-range means a browser
   fetches the cyrillic file only if cyrillic glyphs appear), which is exactly why the weight
   went unnoticed: ~950 KB of binaries per rebuild, for alphabets this window has never drawn.
   latin-ext keeps accented Latin covered, which agent names and prose actually use.
   Text outside those ranges falls back to Segoe UI rather than going missing. */
import '@fontsource/bricolage-grotesque/latin-600.css'
import '@fontsource/bricolage-grotesque/latin-700.css'
import '@fontsource/bricolage-grotesque/latin-800.css'
import '@fontsource/bricolage-grotesque/latin-ext-600.css'
import '@fontsource/bricolage-grotesque/latin-ext-700.css'
import '@fontsource/bricolage-grotesque/latin-ext-800.css'
import '@fontsource/hanken-grotesk/latin-400.css'
import '@fontsource/hanken-grotesk/latin-500.css'
import '@fontsource/hanken-grotesk/latin-600.css'
import '@fontsource/hanken-grotesk/latin-700.css'
import '@fontsource/hanken-grotesk/latin-ext-400.css'
import '@fontsource/hanken-grotesk/latin-ext-500.css'
import '@fontsource/hanken-grotesk/latin-ext-600.css'
import '@fontsource/hanken-grotesk/latin-ext-700.css'
import '@fontsource/jetbrains-mono/latin-400.css'
import '@fontsource/jetbrains-mono/latin-500.css'
import '@fontsource/jetbrains-mono/latin-600.css'
import '@fontsource/jetbrains-mono/latin-ext-400.css'
import '@fontsource/jetbrains-mono/latin-ext-500.css'
import '@fontsource/jetbrains-mono/latin-ext-600.css'

import App from './App'
import './styles.css'

const host = document.getElementById('root')
// Not a fallback — a hard stop. A missing mount point means index.html and this entry disagree,
// and a page that silently renders nothing is the hardest kind of build error to find.
if (!host) throw new Error('#root is missing from index.html')

createRoot(host).render(<App />)

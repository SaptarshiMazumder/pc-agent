/* Figure Creator — the agent's own window.
 *
 * There is almost nothing here, and that is the design. The window IS the agentd shell: the same
 * conversation components, the same canvas (viewers, the fabric annotate/vector/PNG editor, the
 * workspace tree), the same stylesheet — mounted from `@agentd/canvas`, which bundles the
 * shell's own sources rather than a copy of them. This file's whole job is the three facts the
 * bundle cannot know: WHO is using it, WHICH agent it drives, and what it is called.
 *
 * WHAT THIS REPLACED. A hand-written stage + log + textarea, ~300 lines, that re-derived a
 * fraction of the shell's chat: no thinking blocks, no tool blocks, no sub-agent grouping, no
 * date separators, no attachments, no canvas. Every one of those was a feature the product
 * already had and this window did not, and each would have had to be written again — and then
 * kept in step. Now a feature landing in the shell lands here on the next build.
 *
 * b5: the SDK's shared sign-in gate replaced a hand-rolled login (see the agent's CHANGELOG note
 *     in agent.toml).
 * b6: that gate is REQUIRED, and the window became the shell surface.
 */

const BUILD = 'b6-shell-surface'
console.log('figure-creator app', BUILD)

const AGENT_ID = 'figure-creator'
const TITLE = 'Figure Creator'

const client = agentd.fromPage({ clientName: 'figure-creator-app/2' })

let surface = null

/**
 * WHO IS SIGNED IN, and the way out — supplied by the app because both halves need a credential,
 * and the surface bundle deliberately never touches one.
 *
 * `credits` is the accounts service's own `/me/credits`, the ONE money endpoint a client may call
 * (it resolves the account from the token, so there is no parameter to tamper with). The token
 * comes from the manager rather than storage, so a spent one is renewed before the call instead
 * of returning a 401 the footer would have to interpret.
 */
function accountAdapter() {
  const manager = agentd.identity({ client })
  return {
    email: manager.current()?.email || '',
    async credits() {
      const base = await agentd.accountsUrl({ client })
      if (!base) return null // a deployment with no accounts service does not meter
      const token = await manager.accessToken()
      if (!token) return null
      const r = await fetch(base.replace(/\/$/, '') + '/me/credits', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (!r.ok) return null
      const d = await r.json()
      return Number(d.credits_remaining || 0)
    },
    /** The catalogue is PUBLIC — no token — and it carries the rail's own description of what a
     *  purchase does, which the page prints verbatim rather than interpreting. */
    async products() {
      const base = await agentd.accountsUrl({ client })
      if (!base) return { products: [], provider: '', note: '' }
      const r = await fetch(base.replace(/\/$/, '') + '/products?kind=credit_pack')
      if (!r.ok) return { products: [], provider: '', note: '' }
      const d = await r.json()
      return { products: d.products || [], provider: String(d.provider || ''), note: String(d.payment_note || '') }
    },
    /** ONE product id, nothing else. Price and credit count are read server-side from the
     *  products row, so a client cannot post itself a fortune. The idempotency key is per press:
     *  a double-click or a retried request buys one pack, not two. */
    async buy(productId) {
      const base = await agentd.accountsUrl({ client })
      const token = await manager.accessToken()
      if (!base || !token) throw new Error('sign in to buy credits')
      const r = await fetch(base.replace(/\/$/, '') + '/me/purchase', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          product_id: productId,
          idempotency_key: `fc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
        })
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(String(d.detail || `purchase failed (HTTP ${r.status})`))
      return {
        credits: Number(d.credits || 0),
        creditsRemaining: Number(d.credits_remaining || 0),
        detail: String((d.payment || {}).detail || '')
      }
    },
    async signOut() {
      // authLogout revokes with the accounts service and drops the pair; the identity
      // subscription below sees the manager go empty and re-gates this window.
      await agentd.authLogout({ client })
    }
  }
}

/** Mount the product window. Idempotent: a re-gate after a signed-out spell re-enters here,
 *  and mounting twice over one element would leave two React roots fighting over it. */
function mountSurface() {
  if (surface) return
  surface = agentdCanvas.mountShell(document.getElementById('root'), {
    client,
    agentId: AGENT_ID,
    title: TITLE,
    account: accountAdapter(),
    blurb:
      'Describe a figure — a mechanism, a pathway, an anatomy plate, a process. It renders ' +
      'publication-grade artwork with editable labels, and opens beside you to annotate or ' +
      'convert to vector.',
    suggestions: [
      'The stages of mitosis, clean shaded style',
      'A labeled cross-section of a plant leaf',
      'How mRNA vaccines work, as a flowchart'
    ]
  })
}

function unmountSurface() {
  surface?.unmount()
  surface = null
}

/**
 * One awaited call decides everything: a stored session passes straight through, anything else
 * meets the platform's own sign-in form (same accounts service, same email + password, same
 * account as the agentd shell — identity is one thing across this platform, never per-app).
 *
 * `require: true` is the whole policy. Without it the gate steps aside wherever the DAEMON does
 * not demand a login — every desktop install, where the machine token already authorises the
 * window — and this agent would run as nobody on a laptop and as an account on the web, which is
 * two products. Every generation here costs money and lands in somebody's workspace, so the
 * answer has to be the same in both places: sign in first.
 *
 * Past the await the gate is FINISHED, which is not the same as somebody being behind it — a
 * daemon with no accounts service configured can offer no form at all. So this reads `signedIn`
 * and mounts nothing rather than assuming.
 */
async function bootstrap() {
  let auth
  try {
    auth = await agentd.mountSignInGate({
      client,
      require: true,
      blurb: 'Figure Creator runs on your agentd account — the same one you use everywhere else.'
    })
  } catch (e) {
    // The daemon itself is unreachable — say so plainly rather than leaving a dead page.
    document.getElementById('root').innerHTML =
      '<div class="app-fatal">Cannot reach the agentd service. Is the daemon running?</div>'
    console.warn('[sign-in]', (e && e.message) || e)
    return
  }
  if (auth.signedIn) mountSurface()
}

// RE-GATE WHEN THE CREDENTIAL GOES AWAY. A window the shell opened is handed an access token on
// its launch url and no refresh token (deliberately — a refresh token is a 30-day credential for
// the whole account, and an agent app is third-party code). Nothing can renew that: on desktop the
// shell feeds it, and in a browser tab nothing does, so ~10 minutes in the manager finds it spent
// and evicts it. The daemon does not refuse a dead token, it accepts the reconnect ANONYMOUSLY —
// so without this the window looks signed in and every send lands as nobody. One subscription
// turns that into the one visible prompt it should be.
agentd.identity({ client }).subscribe((pair) => {
  if (pair) return
  unmountSurface()
  void bootstrap()
})

void bootstrap()

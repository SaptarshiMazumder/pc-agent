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
 * Sign-in uses the current shared React card, exposed by the canvas bundle. The page's SDK
 * owns identity and credentials; this file only decides which surface should be visible.
 */

const BUILD = '1.3.5-current-auth'
console.log('figure-creator app', BUILD)

const AGENT_ID = 'figure-creator'
const TITLE = 'Figure Creator'

let client = null
let surface = null
let signInSurface = null
let surfaceAccountId = ''
let lastAccountId
let authRequest = null
let authRequested = false

/**
 * WHO IS SIGNED IN, and the way out — supplied by the app because both halves need a credential,
 * and the surface bundle deliberately never touches one.
 *
 * `credits` is the accounts service's own `/me/credits`, the ONE money endpoint a client may call
 * (it resolves the account from the token, so there is no parameter to tamper with). The token
 * comes from the manager rather than storage, so a spent one is renewed before the call instead
 * of returning a 401 the footer would have to interpret.
 */
function accountAdapter(account) {
  const manager = agentd.identity({ client })
  return {
    email: account.email || '',
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
      await agentd.authLogout({ client })
      // authLogout invalidates the SDK cache; a fresh read closes the shell and its socket.
      // Re-read explicitly too: hosted logout has no runtime auth.changed broadcast.
      await refreshAuth()
    }
  }
}

/** Mount the product window. Idempotent: a re-gate after a signed-out spell re-enters here,
 *  and mounting twice over one element would leave two React roots fighting over it. */
function mountSurface(account) {
  if (surface && surfaceAccountId === account.accountId) return
  unmountViews()
  surfaceAccountId = account.accountId
  surface = agentdCanvas.mountShell(document.getElementById('root'), {
    client,
    agentId: AGENT_ID,
    title: TITLE,
    account: accountAdapter(account),
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

function unmountViews() {
  surface?.unmount()
  signInSurface?.unmount()
  surface = null
  signInSurface = null
  surfaceAccountId = ''
  document.getElementById('root').replaceChildren()
}

function mountSignIn() {
  if (signInSurface) return
  unmountViews()
  signInSurface = agentdCanvas.mountSignIn(document.getElementById('root'), {
    product: TITLE,
    onDone: () => void refreshAuth(),
  })
}

/** A failed probe is not a sign-out. Keep an open workspace (including its draft) and show a
 * retry banner; at launch, show the same explanation as a full-page state instead. */
function showStatus(message, retry = false) {
  const notice = document.getElementById('app-status')
  let host = notice
  if (!surface) {
    unmountViews()
    host = document.createElement('main')
    host.className = 'app-fatal'
    host.setAttribute('role', 'status')
    document.getElementById('root').append(host)
    notice.hidden = true
  } else {
    notice.hidden = false
  }
  host.replaceChildren()
  const text = document.createElement('p')
  text.textContent = message
  host.append(text)
  if (retry) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'btn'
    button.textContent = 'Retry'
    button.addEventListener('click', () => {
      button.disabled = true
      void refreshAuth()
    })
    host.append(button)
  }
}

async function checkAuth() {
  // The typed identity answer distinguishes an expired session from a temporary outage.
  // authStatus().signedIn alone collapses both into false and would erase an open workspace.
  const [platform, account] = await agentd.withTimeout(Promise.all([
    agentd.platformStatus({ timeoutMs: 10000 }),
    agentd.identity({ client }).state(),
  ]), 10000, 'Checking sign-in')

  // An identity notification during this probe supersedes its answer. The queued probe in
  // refreshAuth will render the latest account, without briefly mounting the previous one.
  if (authRequested) return

  if (account.state === 'accounts_unreachable') {
    showStatus('The sign-in service is temporarily unavailable. Please retry.', true)
    return
  }

  document.getElementById('app-status').hidden = true
  if (account.state === 'ok') {
    // A different account needs a fresh socket and React root: neither its transcript nor its
    // draft may be inherited from the previous user. Ordinary refreshes keep both intact.
    if (lastAccountId !== undefined && lastAccountId !== account.accountId) client.reconnect()
    lastAccountId = account.accountId
    mountSurface(account)
  } else {
    lastAccountId = ''
    client.close()
    if (platform.accountsUrl) mountSignIn()
    else showStatus('Sign-in is required, but this daemon has no accounts service configured.', true)
  }
}

/** Coalesce identity and socket notifications. Remember a change during an in-flight probe so
 * its old answer cannot win over a subsequent sign-out or account switch. */
function refreshAuth() {
  if (authRequest) {
    authRequested = true
    return authRequest
  }
  authRequest = checkAuth()
    .catch((error) => {
      if (authRequested) return
      console.warn('[figure-creator startup]', error)
      showStatus('Cannot connect to Figure Creator. Check the service and retry.', true)
    })
    .finally(() => {
      authRequest = null
      if (authRequested) {
        authRequested = false
        void refreshAuth()
      }
    })
  return authRequest
}

function start() {
  try {
    client = agentd.fromPage({ clientName: 'figure-creator-app/3' })
    agentd.onIdentityChanged(() => void refreshAuth())
    client.on('auth.changed', () => {
      agentd.identity({ client }).forget()
      void refreshAuth()
    })
    // Another browser window may sign in/out without a runtime broadcast. Recheck when this
    // window returns to the foreground, using the same SDK session source as initial launch.
    window.addEventListener('focus', () => {
      agentd.identity({ client }).forget()
      void refreshAuth()
    })
    void refreshAuth()
  } catch (error) {
    console.error('[figure-creator startup]', error)
    showStatus('Figure Creator could not start. Reload the page to load the latest app files.')
  }
}

start()

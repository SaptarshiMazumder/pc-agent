/**
 * The sign-in GATE — the UI half of sign-in (mechanism lives in auth.ts).
 *
 *   await agentd.mountSignInGate()
 *   // past this line somebody is signed in, or this install has no accounts to sign in to
 *
 * No arguments needed: the heading comes from the page's own <title>, which is already this
 * agent's name. Naming a product here would be a second copy of it to keep in sync.
 *
 * ONE LINE, and it is deliberately a blocking await: an app that renders its composer first and
 * signs in later has to handle "signed in yet?" at every send site.
 *
 * IT RENDERS NOTHING in exactly two cases:
 *   - this daemon has no accounts service configured, so there is nothing to sign in to
 *   - somebody is already signed in
 *
 * THAT LIST USED TO BE LONGER, AND WRONG. It also skipped the gate whenever the platform's keys
 * were already paying for model calls — because this was never a login, it was a checkout screen
 * ("Runs on our servers — no API keys to set up"). On a BYOK install nobody is paying, so the
 * gate concluded there was nothing to ask and rendered nothing, forever. Signing in and paying
 * are now separate questions; this one only asks who you are.
 *
 * ELEMENT IDS ARE PART OF THE CONTRACT. `gate`, `gateForm`, `gateEmail`, `gatePass` match what
 * figure-creator's hand-written gate used, because the desktop shell's AGENTD_E2E_LOGIN hook
 * drives those ids to test a packaged build with no human at the keyboard. Renaming them would
 * silently disable that test — it would fill nothing and still report success.
 *
 * STYLING is injected once and driven by CSS custom properties, so an agent themes it from its
 * own stylesheet (`--gate-accent`, `--gate-bg`, …) instead of forking the markup. Values fall
 * back to the surrounding page's, so an unthemed agent still looks like itself.
 */

import {
  AuthOptions,
  AuthState,
  authLogin,
  authLogout,
  authStatus,
  acceptHostTokens,
  startAuthRenewal
} from './auth'

export interface SignInGateOptions extends AuthOptions {
  /** Product name in the heading. Defaults to the document title, then the agent id. */
  product?: string
  /** One line under the heading explaining WHY a sign-in is being asked for. */
  blurb?: string
  /** Where to attach. Defaults to document.body. */
  mount?: HTMLElement
  /** Allow account creation from the gate (default true). Set false for invite-only products. */
  allowSignup?: boolean
}

export interface GateResult extends AuthState {
  /** true when a gate was actually displayed and the user completed it. */
  signedInHere: boolean
}

const STYLE_ID = 'agentd-gate-style'

const CSS = `
.agentd-gate{position:fixed;inset:0;z-index:9999;display:grid;place-items:center;
  background:var(--gate-bg,rgba(20,20,22,.72));backdrop-filter:blur(6px);
  font-family:var(--gate-font,system-ui,-apple-system,Segoe UI,sans-serif)}
.agentd-gate[hidden]{display:none}
.agentd-gate-card{width:min(92vw,360px);padding:26px 24px;border-radius:14px;
  background:var(--gate-card,#fff);color:var(--gate-fg,#16161a);
  box-shadow:0 18px 50px rgba(0,0,0,.35);display:flex;flex-direction:column;gap:10px}
.agentd-gate-mark{font-size:26px;line-height:1;text-align:center;color:var(--gate-accent,#4f46e5)}
.agentd-gate-title{margin:2px 0 0;font-size:19px;font-weight:650;text-align:center}
.agentd-gate-sub{margin:0 0 6px;font-size:12.5px;line-height:1.45;text-align:center;
  color:var(--gate-muted,#6b6b76)}
.agentd-gate-label{font-size:11.5px;font-weight:600;color:var(--gate-muted,#6b6b76)}
.agentd-gate-input{padding:9px 11px;border-radius:8px;font-size:13.5px;
  border:1px solid var(--gate-border,#d8d8e0);background:var(--gate-input,#fff);
  color:var(--gate-fg,#16161a)}
.agentd-gate-input:focus{outline:2px solid var(--gate-accent,#4f46e5);outline-offset:1px}
.agentd-gate-btn{margin-top:6px;padding:10px 12px;border:0;border-radius:8px;cursor:pointer;
  font-size:13.5px;font-weight:600;color:var(--gate-on-accent,#fff);
  background:var(--gate-accent,#4f46e5)}
.agentd-gate-btn[disabled]{opacity:.6;cursor:default}
.agentd-gate-toggle{padding:4px;border:0;background:none;cursor:pointer;font-size:12px;
  color:var(--gate-muted,#6b6b76);text-decoration:underline}
.agentd-gate-error{padding:7px 9px;border-radius:7px;font-size:12px;
  background:var(--gate-error-bg,#fdeaea);color:var(--gate-error-fg,#a3232b)}
.agentd-gate-error[hidden]{display:none}
`

function injectStyle(): void {
  if (document.getElementById(STYLE_ID)) return
  const el = document.createElement('style')
  el.id = STYLE_ID
  el.textContent = CSS
  document.head.appendChild(el)
}

function build(product: string, blurb: string, allowSignup: boolean): HTMLElement {
  const wrap = document.createElement('div')
  wrap.className = 'agentd-gate'
  wrap.id = 'gate'
  wrap.innerHTML = `
    <form class="agentd-gate-card" id="gateForm" autocomplete="on">
      <div class="agentd-gate-mark" aria-hidden="true">&#9681;</div>
      <h1 class="agentd-gate-title" id="gateTitle">Sign in</h1>
      <p class="agentd-gate-sub" id="gateSub"></p>
      <label class="agentd-gate-label" for="gateEmail">Email</label>
      <input class="agentd-gate-input" id="gateEmail" type="email" autocomplete="email"
             required placeholder="you@example.com" />
      <label class="agentd-gate-label" for="gatePass">Password</label>
      <input class="agentd-gate-input" id="gatePass" type="password"
             autocomplete="current-password" required minlength="8" placeholder="••••••••" />
      <div class="agentd-gate-error" id="gateError" hidden></div>
      <button class="agentd-gate-btn" id="gateBtn" type="submit">Sign in</button>
      ${allowSignup ? '<button class="agentd-gate-toggle" id="gateToggle" type="button">New here? Create an account</button>' : ''}
    </form>`
  // textContent, not innerHTML: product and blurb are caller-supplied strings and must never be
  // able to inject markup into the page they are gating.
  const title = wrap.querySelector('#gateTitle') as HTMLElement
  const sub = wrap.querySelector('#gateSub') as HTMLElement
  title.textContent = `Sign in to ${product}`
  sub.textContent = blurb
  return wrap
}

/**
 * Sign the user in, showing a form only if one is needed.
 *
 * Resolves once the app may proceed. Rejects only if the daemon itself cannot be reached — a
 * wrong password does not reject, it is reported in the form and the user tries again.
 */
/** `?verify=1` on the page URL — set by the tool that opens an agent's window to check it. */
function wantsVerifyBypass(): boolean {
  if (typeof location === 'undefined') return false
  try {
    return new URL(location.href).searchParams.get('verify') === '1'
  } catch {
    return false
  }
}

export async function mountSignInGate(options: SignInGateOptions = {}): Promise<GateResult> {
  const allowSignup = options.allowSignup !== false
  const product =
    options.product || (typeof document !== 'undefined' && document.title) || 'this app'
  const blurb = options.blurb || 'Sign in to continue.'

  const state = await authStatus(options)
  if (!state.available || state.signedIn) {
    // ALREADY SIGNED IN is the common path — every reload takes it — so renewal has to start
    // here too, not only after a fresh sign-in. Without this, a page that resumes a stored
    // session runs on whatever life is left in that access token and then goes anonymous.
    if (state.signedIn) {
      startAuthRenewal(options)
      // Shell-opened app windows have no refresh token and never see this form; the desktop
      // pushes fresh access tokens down instead. No-op in a browser tab.
      acceptHostTokens(options)
    }
    return { ...state, signedInHere: false }
  }

  // VERIFICATION RUNS PAST THE GATE, and only where the gate was optional anyway.
  //
  // A headless browser is a fresh profile with no stored session, so it meets this form every
  // time — and an automated check that can only ever photograph a login screen verifies nothing.
  // Handing it credentials would mean a real account, on every run, to look at a window the
  // daemon would have served to nobody in particular.
  //
  // `required` is the daemon's own answer to "must anyone sign in here", so this grants exactly
  // nothing that was being withheld: where sign-in IS demanded the flag is ignored and the form
  // appears as always. It removes a prompt, never a check.
  if (!state.required && wantsVerifyBypass()) {
    return { ...state, signedInHere: false }
  }

  injectStyle()
  const gate = build(product, blurb, allowSignup)
  ;(options.mount || document.body).appendChild(gate)

  const $ = (id: string) => gate.querySelector(`#${id}`) as HTMLElement
  const emailEl = $('gateEmail') as HTMLInputElement
  const passEl = $('gatePass') as HTMLInputElement
  const btn = $('gateBtn') as HTMLButtonElement
  const errorEl = $('gateError')
  const subEl = $('gateSub')
  const titleEl = $('gateTitle')
  const toggle = allowSignup ? ($('gateToggle') as HTMLButtonElement) : null

  let signup = false
  const say = (text: string) => (subEl.textContent = text)
  const fail = (text: string) => {
    errorEl.textContent = text
    errorEl.hidden = !text
  }

  // The daemon remembers the last email it signed in with, so a re-prompt after sign-out (or on
  // a second machine window) starts with the field filled. It is not a credential; the token it
  // sits beside never leaves the daemon.
  if (state.email) emailEl.value = state.email
  setTimeout(() => emailEl.focus(), 0)

  toggle?.addEventListener('click', () => {
    signup = !signup
    titleEl.textContent = signup ? 'Create your account' : `Sign in to ${product}`
    btn.textContent = signup ? 'Create account' : 'Sign in'
    toggle.textContent = signup ? 'Have an account? Sign in' : 'New here? Create an account'
    passEl.setAttribute('autocomplete', signup ? 'new-password' : 'current-password')
    say(blurb)
    fail('')
  })

  return new Promise<GateResult>((resolve) => {
    const form = $('gateForm') as HTMLFormElement
    say(blurb)
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault()
      const email = emailEl.value.trim().toLowerCase()
      const password = passEl.value
      if (!email || !password) return
      btn.disabled = true
      fail('')
      say(signup ? 'Creating your account…' : 'Signing in…')
      try {
        const result = await authLogin({ email, password, signup }, options)
        // RENEW FOR AS LONG AS THE PAGE IS OPEN. The access token this just stored lasts about
        // ten minutes; the socket outlives it and reconnects, and an expired token is not
        // refused — the daemon accepts the page ANONYMOUSLY, which shows up as the account's
        // agents silently vanishing. A drop-in gate that leaves the caller to discover that is
        // not a drop-in, so it is started here rather than documented.
        startAuthRenewal(options)
        acceptHostTokens(options)
        gate.remove()
        resolve({ ...result, signedInHere: true })
      } catch (e) {
        // Stay on the form: the daemon is fine, this attempt was not.
        btn.disabled = false
        say(blurb)
        fail(String((e as Error)?.message || e))
      }
    })
  })
}

/** Sign out and show the gate again. Convenience for an app with a Sign-out control. */
export async function signOutAndGate(options: SignInGateOptions = {}): Promise<GateResult> {
  const state = await authLogout(options)
  if (!state.available) return { ...state, signedInHere: false }
  return mountSignInGate(options)
}

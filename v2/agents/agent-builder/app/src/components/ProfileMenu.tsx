/* The account menu, bottom-left — the same control agentd puts there.
 *
 * WHY THE FOOTER AND NOT THE HEADER. Identity is ambient, not part of the work: you check it when
 * something surprises you (a publish attributed to the wrong account, a run that says "out of
 * credits") and otherwise never. agentd pins it to the bottom of the sidebar for that reason, and
 * a user who learns it there should find it in the same corner here.
 *
 * SIGN-IN LIVES HERE, not in Settings. It used to be an "Account" section three scrolls into the
 * settings modal, next to reasoning effort and max turns — which is where you go to change how the
 * thing WORKS, not to say who you are. It is also the first thing a new user needs and the last
 * place they would look.
 *
 * IT READS `auth`, NOT THE IDENTITY CHIP — and that distinction is a bug this file already had.
 * The chip comes from `platform.status` over the socket, and its hook only re-asks when the
 * socket's STATUS changes. Signing in stores a session in this window and calls `reload()`, which
 * moves `auth` and leaves the chip untouched. Wired to the chip, this menu went on offering
 * "Sign in" to somebody who had just signed in, with no way ever to sign out. `auth` is this
 * client's own answer about its own session, which is the question the menu is asking.
 *
 * ORGANIZATIONS LIVES HERE NOW, exactly as it does in agentd's menu — the entry, and the
 * user's own orgs listed under it, each a shortcut straight to that org's page. What is still
 * absent is not an oversight: Account is a page this window has no
 * route to and no permission to serve. Run mode stays in Settings because it is a property of the
 * MACHINE — it applies to every agent on it, not to this account.
 */

import { CreditCard, LogIn, LogOut, User } from 'lucide-react'
import { fetchMyOrgs, type MyOrgs } from '@agentd/client'
import { useEffect, useState } from 'react'

import type { AuthState } from '@agentd/client'

export function ProfileMenu({
  auth,
  error,
  onCredits,
  onOrgs,
  onOrg,
  onSignIn,
  onSignOut,
  variant = 'footer',
}: {
  /** This client's own view of its session. Null until the first read, or after one failed. */
  auth: AuthState | null
  /** Why the account could not be read. Shown here rather than swallowed: an account control that
   *  quietly does nothing is indistinguishable from a build that has no Cloud. */
  error: string
  onCredits: () => void
  /** Open the Organizations overview. */
  onOrgs: () => void
  /** Open ONE organization's page — the shortcut rows under the entry. */
  onOrg: (id: string) => void
  /** Draws the SDK's gate and re-reads the account. Never a form of this window's own. */
  onSignIn: () => Promise<void> | void
  onSignOut: () => Promise<void> | void
  /** Which footer this is sitting in: the full sidebar's, or the collapsed icon rail's. Only the
   *  trigger's shape and the popover's anchor change — agentd switches the same two things. */
  variant?: 'footer' | 'rail'
}) {
  const [open, setOpen] = useState(false)
  const [orgs, setOrgs] = useState<MyOrgs | null>(null)
  useEffect(() => {
    if (!open) return
    void fetchMyOrgs({}).then(setOrgs).catch(() => setOrgs(null))
  }, [open])
  const [busy, setBusy] = useState('')
  const [failed, setFailed] = useState('')

  const signedIn = !!auth?.signedIn
  // No accounts service on this build means there is nobody to sign in AS. The gate knows it and
  // returns without drawing anything, which from the outside is a button that does nothing — so
  // the button is not offered, and the reason is stated where the button would have been.
  const canSignIn = !!auth?.available

  /**
   * One runner for both actions.
   *
   * IT CATCHES. Without this, a gate that could not reach the daemon rejected into an unhandled
   * promise: the menu closed, nothing appeared, and nothing anywhere said why — which is exactly
   * how "the sign in button doesn't do anything" gets reported. `finally` alone does not catch.
   */
  const run = (what: string, fn: () => Promise<void> | void) => async () => {
    setBusy(what)
    setFailed('')
    try {
      await fn()
      setOpen(false)
    } catch (e) {
      setFailed(String((e as Error)?.message || e))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="menu-wrap">
      {/* A backdrop rather than a document listener: it closes on any outside click without this
          menu having to reason about which clicks belong to it. */}
      {open && <div className="menu-backdrop" onClick={() => setOpen(false)} />}
      {variant === 'footer' ? (
        <button
          className={`icon-btn footer-icon ${signedIn ? '' : 'anon'} ${open ? 'menu-trigger-on' : ''}`}
          title={signedIn ? `Signed in as ${auth?.email || 'your account'}` : 'Account'}
          onClick={() => setOpen((v) => !v)}
        >
          <User size={17} />
        </button>
      ) : (
        /* The rail trigger is the ACCOUNT AVATAR, the way the design draws it — a lime tile
           with the account's initial, grey while signed out. The identity fact was already in
           the title; now it is the button. */
        <button
          className={`rail-btn rail-account ${signedIn ? '' : 'anon'} ${open ? 'active' : ''}`}
          title={signedIn ? `Signed in as ${auth?.email || 'your account'}` : 'Account'}
          aria-label="Account"
          onClick={() => setOpen((v) => !v)}
        >
          {signedIn && auth?.email ? (
            <span className="rail-account-tile">{auth.email.slice(0, 2).toUpperCase()}</span>
          ) : (
            <User size={18} />
          )}
        </button>
      )}
      {open && (
        <div className={`app-menu ${variant === 'rail' ? 'app-menu--rail' : 'app-menu--left'}`}>
          <div className="pmenu-head">
            <div className="pmenu-name">{signedIn ? auth?.email || 'Signed in' : 'Local'}</div>
            <div className="pmenu-desc">
              {signedIn
                ? 'Cloud · platform keys, metered'
                : canSignIn
                  ? 'Your own keys (BYOK) — sign in to use Cloud'
                  : 'This build has no accounts service, so there is nobody to sign in as.'}
            </div>
            {(failed || error) && <div className="pmenu-err">{failed || error}</div>}
          </div>

          <button
            className="app-menu-item"
            type="button"
            onClick={() => {
              onCredits()
              setOpen(false)
            }}
          >
            <CreditCard size={16} />
            <span>Credits &amp; billing</span>
          </button>

          <button
            className="app-menu-item"
            type="button"
            onClick={() => {
              onOrgs()
              setOpen(false)
            }}
          >
            <span>Organizations</span>
          </button>
          {(orgs?.orgs || []).map((o) => (
            <button
              key={o.id}
              className="app-menu-item app-menu-item--sub"
              type="button"
              title={`Open ${o.name}`}
              onClick={() => {
                onOrg(o.id)
                setOpen(false)
              }}
            >
              <span>{o.name}</span>
            </button>
          ))}

          {canSignIn && (
            <>
              <div className="pmenu-sep" />
              {signedIn ? (
                <button
                  className="app-menu-item"
                  type="button"
                  disabled={!!busy}
                  onClick={run('out', onSignOut)}
                >
                  <LogOut size={16} />
                  <span>{busy === 'out' ? 'Signing out…' : 'Sign out'}</span>
                </button>
              ) : (
                <button
                  className="app-menu-item"
                  type="button"
                  disabled={!!busy}
                  onClick={run('in', onSignIn)}
                >
                  <LogIn size={16} />
                  <span>{busy === 'in' ? 'Signing in…' : 'Sign in'}</span>
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

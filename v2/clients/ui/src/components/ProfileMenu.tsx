import { useState } from 'react'
import { CreditCard, LayoutGrid, LogOut, Monitor, User } from 'lucide-react'

import { gateway } from '../gateway/client'
import { signOut, useAuthSession } from '../lib/auth'
import { setMode, useMode } from '../lib/mode'
import { isDesktop } from '../lib/platform'
import { useApp } from '../state/store'

/**
 * The bottom-of-sidebar profile/account menu — the SINGLE home for identity + mode.
 *
 * Account identity is a CLOUD concept, so it only appears when signed in: profile icon, email,
 * Account + Credits shortcuts, and Sign out. Desktop Local mode shows no account — just "Switch".
 * "Switch mode" returns to the main launcher (setMode(null) → App shows <Launcher/>), where you
 * pick Local or Cloud — deliberately NOT an inline toggle, and DESKTOP-ONLY: the web build has no
 * BYOK mode to switch to.
 *
 * WEB USED TO GET NOTHING HERE. This component bailed out on `!isDesktop`, which on the hosted web
 * client — where every user is signed in and metered — removed the only route to the account page
 * and the only way to sign out. Mode is the desktop-only part, not identity.
 */
export default function ProfileMenu({ variant }: { variant: 'footer' | 'rail' }): JSX.Element | null {
  const setView = useApp((s) => s.setView)
  const session = useAuthSession()
  const mode = useMode()
  const [open, setOpen] = useState(false)

  // On the web there is no Local mode: being signed in IS being in the cloud.
  const signedIn = !!session && (!isDesktop || mode === 'cloud')
  // Nothing to show on the web when signed out — the sign-in gate is already the whole screen.
  if (!isDesktop && !signedIn) return null
  const TriggerIcon = signedIn ? User : Monitor

  function toLauncher(): void {
    setOpen(false)
    setMode(null) // App re-renders into the launcher to re-choose Local / Cloud
  }

  /**
   * Sign out of BOTH halves. There used to be only one.
   *
   * `signOut()` clears this client's own stored session and nothing else, so the DAEMON kept its
   * identity token and its model-proxy credential. The window said "signed out" while the daemon
   * stayed authenticated and carried on metering the account — and because Cloud only needs the
   * daemon's token, the run-mode switch happily turned platform billing back on for an account
   * the UI insisted you had left.
   *
   * `auth.logout` is the half that means it: the daemon drops the identity and re-applies the run
   * mode, which unbinds the proxy in the same step.
   */
  async function doSignOut(): Promise<void> {
    setOpen(false)
    try {
      await gateway.request('auth.logout')
    } catch {
      /* older daemon without auth.* — the local sign-out below still happens */
    }
    signOut()
    setMode(null) // signing out leaves Cloud → back to the launcher to choose
    location.reload() // drop the account-scoped connection cleanly
  }

  const trigger =
    variant === 'footer' ? (
      <button
        className={`icon-btn footer-icon ${open ? 'menu-trigger-on' : ''}`}
        title={signedIn ? `Account · ${session.email}` : 'Local mode'}
        onClick={() => setOpen((v) => !v)}
      >
        <TriggerIcon size={17} />
      </button>
    ) : (
      <button
        className={`rail-btn ${open ? 'active' : ''}`}
        title={signedIn ? `Account · ${session.email}` : 'Local mode'}
        onClick={() => setOpen((v) => !v)}
      >
        <TriggerIcon size={18} />
      </button>
    )

  return (
    <div className="menu-wrap">
      {open && <div className="menu-backdrop" onClick={() => setOpen(false)} />}
      {trigger}
      {open && (
        <div className={`app-menu ${variant === 'rail' ? 'app-menu--rail' : 'app-menu--left'}`}>
          <div className="pmenu-head">
            <div className="pmenu-name">{signedIn ? session.email : 'Local'}</div>
            <div className="pmenu-desc">
              {signedIn ? 'Cloud · platform keys, metered' : 'Your own keys (BYOK)'}
            </div>
          </div>

          {signedIn && (
            <>
              <button
                className="app-menu-item"
                type="button"
                onClick={() => {
                  setView('account')
                  setOpen(false)
                }}
              >
                <User size={16} />
                <span>Account</span>
              </button>
              <button
                className="app-menu-item"
                type="button"
                onClick={() => {
                  setView('subscription')
                  setOpen(false)
                }}
              >
                <CreditCard size={16} />
                <span>Credits &amp; billing</span>
              </button>
              <button className="app-menu-item" type="button" onClick={() => void doSignOut()}>
                <LogOut size={16} />
                <span>Sign out</span>
              </button>
              {isDesktop && <div className="pmenu-sep" />}
            </>
          )}

          {/* Mode is a desktop concept: there is no BYOK web build to switch to. */}
          {isDesktop && (
            <button className="app-menu-item" type="button" onClick={toLauncher}>
              <LayoutGrid size={16} />
              <span>Switch mode</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}

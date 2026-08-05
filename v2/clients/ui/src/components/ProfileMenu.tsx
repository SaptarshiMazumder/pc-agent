import { useState } from 'react'
import { CreditCard, LayoutGrid, LogOut, Monitor, User } from 'lucide-react'

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

  function doSignOut(): void {
    setOpen(false)
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
              <button className="app-menu-item" type="button" onClick={doSignOut}>
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

import { useState } from 'react'
import { LayoutGrid, LogOut, Monitor, User } from 'lucide-react'

import { signOut, useAuthSession } from '../lib/auth'
import { setMode, useMode } from '../lib/mode'
import { isDesktop } from '../lib/platform'
import { useApp } from '../state/store'

/**
 * The bottom-of-sidebar profile/account menu — the SINGLE home for identity + mode.
 *
 * Account identity is a CLOUD concept, so it only appears in Cloud mode (signed in): profile
 * icon, email, an Account shortcut, and Sign out. Local mode shows no account — just "Switch".
 * "Switch mode" returns to the main launcher (setMode(null) → App shows <Launcher/>), where you
 * pick Local or Cloud — deliberately NOT an inline toggle. Desktop-only.
 */
export default function ProfileMenu({ variant }: { variant: 'footer' | 'rail' }): JSX.Element | null {
  const setView = useApp((s) => s.setView)
  const session = useAuthSession()
  const mode = useMode()
  const [open, setOpen] = useState(false)

  if (!isDesktop) return null
  const signedIn = mode === 'cloud' && !!session // account exists only in Cloud mode
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
              <button className="app-menu-item" type="button" onClick={doSignOut}>
                <LogOut size={16} />
                <span>Sign out</span>
              </button>
              <div className="pmenu-sep" />
            </>
          )}

          <button className="app-menu-item" type="button" onClick={toLauncher}>
            <LayoutGrid size={16} />
            <span>Switch mode</span>
          </button>
        </div>
      )}
    </div>
  )
}

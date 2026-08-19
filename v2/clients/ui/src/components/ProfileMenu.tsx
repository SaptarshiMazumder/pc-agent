import { useEffect, useState } from 'react'
import { Building2, CreditCard, LayoutGrid, LogOut, Monitor, User } from 'lucide-react'

import { gateway } from '../gateway/client'
import { signOut, useAuthSession } from '../lib/auth'
import { setMode, useMode } from '../lib/mode'
import { fetchMyOrgs, type MyOrgs } from '../lib/orgs'
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
  const viewOrg = useApp((s) => s.viewOrg)
  const session = useAuthSession()
  const mode = useMode()
  const [open, setOpen] = useState(false)
  const [orgs, setOrgs] = useState<MyOrgs | null>(null)

  // On the web there is no Local mode: being signed in IS being in the cloud.
  const signedIn = !!session && (!isDesktop || mode === 'cloud')

  // The switcher's data, fetched when the menu opens (never on a timer — this is décor until
  // clicked). A failure just renders no org section; the Organizations page reports errors.
  useEffect(() => {
    if (!open || !signedIn) return
    let live = true
    fetchMyOrgs()
      .then((d) => {
        if (live) setOrgs(d)
      })
      .catch(() => {
        if (live) setOrgs(null)
      })
    return () => {
      live = false
    }
  }, [open, signedIn])
  // Nothing to show on the web when signed out — the sign-in gate is already the whole screen.
  if (!isDesktop && !signedIn) return null
  const TriggerIcon = signedIn ? User : Monitor

  function toLauncher(): void {
    setOpen(false)
    setMode(null) // App re-renders into the launcher to re-choose Local / Cloud
  }

  /**
   * Sign out. Clearing this client's session IS the whole job now.
   *
   * There used to be an `auth.logout` call here for a daemon that held the identity itself. No
   * such method exists — the daemon keeps nothing and reads `?session=` off each socket — so
   * dropping the stored session and rebuilding the connection (inside `signOut`) is what makes
   * the daemon see an anonymous client. The reload then clears account-scoped view state.
   */
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

              {/* ORGANIZATIONS (tenancy E5): memberships jump straight to their page; the
                  header row opens the overview (create / join / redeem an invite). A “·” dot
                  marks a pending domain-matched join offer so it is discoverable from here. */}
              <button
                className="app-menu-item"
                type="button"
                title="Your organizations — shared agents and a shared credit pool"
                onClick={() => {
                  viewOrg('')
                  setOpen(false)
                }}
              >
                <Building2 size={16} />
                <span>
                  Organizations
                  {orgs && orgs.joinable.length > 0 && <span className="org-offer-dot"> ·</span>}
                </span>
              </button>
              {orgs?.orgs.map((o) => (
                <button
                  key={o.id}
                  className="app-menu-item app-menu-item--sub"
                  type="button"
                  title={`${o.name} — you are ${o.role}`}
                  onClick={() => {
                    viewOrg(o.id)
                    setOpen(false)
                  }}
                >
                  <span className="org-switch-name">{o.name}</span>
                </button>
              ))}

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

/**
 * ADMIN CONSOLE — the platform operator's own page, and its own document.
 *
 * NOT A VIEW INSIDE THE APP. `admin.html` is a second vite entry, so this ships as its own bundle
 * with none of the chat shell in it: no sidebar, no canvas, no agent roster. The operator surface
 * and the product surface stop sharing a page, which is what makes `admin.<domain>` a hostname
 * that means something rather than a redirect into a tab.
 *
 * WHY IT STILL NEEDS THE DAEMON. Most of the console talks to the accounts service over plain
 * fetch (users, credits, creators — see lib/admin), but the Defaults tab writes the MASTER config
 * every account inherits, and only the daemon can write that file. So this page opens the same
 * WebSocket the app does, via the same store, and `bootstrap()` below is the same call App makes.
 * Dropping it would leave the most important tab in the console permanently unable to save.
 *
 * AUTHORIZATION IS NOT HERE, and never was. The gate below decides what to DRAW; every call is
 * re-checked server-side — `whoami().is_admin` for the accounts half, AGENTD_ADMIN_IDENTITIES for
 * `config.set {target:'master'}`. Reaching this URL as a non-admin gets you a page that renders
 * its refusals, not access.
 *
 * DESKTOP IS UNAFFECTED. Electron loads the renderer from `file://` and has no second document to
 * navigate to, so the desktop client keeps Admin as an in-app view (App.tsx). This page is the WEB
 * console; the sidebar sends web users here and desktop users to the view.
 */

import { useEffect } from 'react'
import { LogOut, ShieldAlert } from 'lucide-react'

import AdminView from './components/AdminView'
import SignIn from './components/SignIn'
import { isAccountsMode, signOut, useAuthSession } from './lib/auth'
import { useIsAdmin } from './lib/admin'
import { installSoftScroll } from './lib/softScroll'
import { useApp } from './state/store'

export default function AdminApp() {
  const bootstrap = useApp((s) => s.bootstrap)
  const session = useAuthSession()
  const admin = useIsAdmin()

  // The daemon connection, for the Defaults tab — held back until the GATE HAS PASSED, not
  // merely until sign-in. Connecting for a refused visitor opened a WebSocket on the very page
  // that then had nothing to say over it, and every non-admin who found the URL held one open.
  useEffect(() => {
    if (session && admin) void bootstrap()
  }, [bootstrap, session, admin])

  useEffect(() => installSoftScroll(), [])

  if (isAccountsMode() && !session) return <SignIn />

  // NOT A SECURITY CHECK — the services refuse a non-admin on their own. It is here so somebody
  // who followed a link gets a sentence instead of a console full of empty tables and errors.
  // WITH A DOOR OUT: naming the account and offering sign-out is what turns "wrong account" from
  // a dead end into a two-click fix — without it, someone signed in as the wrong user was simply
  // stuck, with nothing on the page they could act on.
  if (!admin) {
    return (
      <div className="settings">
        <div className="settings-inner settings-wide">
          <div className="settings-empty">
            <ShieldAlert size={20} />
            <p>
              {session?.email ? (
                <>
                  <strong>{session.email}</strong> is not an administrator of this deployment.
                </>
              ) : (
                'This account is not an administrator of this deployment.'
              )}
            </p>
            <button
              className="btn"
              onClick={() => {
                signOut()
                location.reload()
              }}
              title="Sign out and return to the sign-in form — use it to switch to an admin account"
            >
              <LogOut size={14} />
              Sign out
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="app admin-standalone">
      <AdminView />
    </div>
  )
}

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
import { ShieldAlert } from 'lucide-react'

import AdminView from './components/AdminView'
import SignIn from './components/SignIn'
import { isAccountsMode, useAuthSession } from './lib/auth'
import { useIsAdmin } from './lib/admin'
import { installSoftScroll } from './lib/softScroll'
import { useApp } from './state/store'

export default function AdminApp() {
  const bootstrap = useApp((s) => s.bootstrap)
  const session = useAuthSession()
  const admin = useIsAdmin()

  // The daemon connection, for the Defaults tab. Held back until there IS a session, exactly as
  // the app does: connecting first would open a socket for someone the gate is about to stop.
  const needsSignIn = isAccountsMode() && !session
  useEffect(() => {
    if (!needsSignIn) void bootstrap()
  }, [bootstrap, needsSignIn])

  useEffect(() => installSoftScroll(), [])

  if (needsSignIn) return <SignIn />

  // NOT A SECURITY CHECK — the services refuse a non-admin on their own. It is here so somebody
  // who followed a link gets a sentence instead of a console full of empty tables and errors.
  if (!admin) {
    return (
      <div className="settings">
        <div className="settings-inner settings-wide">
          <div className="settings-empty">
            <ShieldAlert size={20} />
            <p>This account is not an administrator of this deployment.</p>
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

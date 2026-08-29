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

import AdminView from './components/AdminView'
import SignIn from './components/SignIn'
import { isAccountsMode, useAuthSession } from './lib/auth'
import { useIsAdmin } from './lib/admin'
import { installSoftScroll } from './lib/softScroll'
import { useApp } from './state/store'

/** Where a NON-admin belongs: the main platform. Derived, never configured — on
 *  `admin.<domain>` the apex is the hostname minus its first label; served at `/admin` on the
 *  app's own host it is simply the root. Works for any domain, including the next one. */
function platformHome(): string {
  const h = typeof location !== 'undefined' ? location.hostname : ''
  if (h.startsWith('admin.')) {
    const port = location.port ? `:${location.port}` : ''
    return `${location.protocol}//${h.slice('admin.'.length)}${port}/`
  }
  return '/'
}

export default function AdminApp() {
  const bootstrap = useApp((s) => s.bootstrap)
  const session = useAuthSession()
  const admin = useIsAdmin() // true | false | null — null is "still asking", and must not act

  // The daemon connection, for the Defaults tab — held back until the GATE HAS PASSED, not
  // merely until sign-in. Connecting for a refused visitor opened a WebSocket on the very page
  // that then had nothing to say over it, and every non-admin who found the URL held one open.
  useEffect(() => {
    if (session && admin === true) void bootstrap()
  }, [bootstrap, session, admin])

  // A CONFIRMED non-admin does not get a page here at all — they are sent to the main platform,
  // where their account actually lives. `replace`, not assign: the console's address must not
  // sit in their history as a Back-button bounce loop. Only on `false` — `null` is the answer
  // still in flight (or unanswerable during an outage), and redirecting on it would bounce every
  // real admin whose whoami had not landed yet.
  useEffect(() => {
    if (session && admin === false) location.replace(platformHome())
  }, [session, admin])

  useEffect(() => installSoftScroll(), [])

  if (isAccountsMode() && !session) return <SignIn />

  // Still asking, or a confirmed non-admin mid-redirect: a quiet beat, never a refusal page —
  // there is nothing here for a non-admin to read, and nothing an admin needs before the answer.
  if (admin !== true) {
    return (
      <div className="settings">
        <div className="settings-inner settings-wide">
          <div className="settings-empty">
            <p>Loading…</p>
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

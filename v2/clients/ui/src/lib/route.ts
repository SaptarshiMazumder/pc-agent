/**
 * URL <-> view. The shell's destinations become addresses.
 *
 * Until now `view` lived only in memory, so /admin could not be opened, bookmarked, sent to a
 * colleague or reloaded — every refresh landed on the default page, and the browser's Back button
 * did nothing. The pieces to fix that were already in place: nginx serves `try_files $uri
 * /index.html`, so any path already resolves to the app; the app simply never read the path.
 *
 * SCOPE, deliberately small. Only the TOP-LEVEL destinations get addresses — the ones a person
 * would type, bookmark or link. The views that name a thing (`agent`, `project`, `app`, `org`)
 * carry an id in memory rather than in the path, so they are left alone here instead of being
 * given a URL that would restore the page but not its subject. That is a follow-up, not a
 * half-measure to ship by accident.
 *
 * DESKTOP IS EXCLUDED, and that is the load-bearing guard: Electron loads the renderer from
 * `file://`, where `history.pushState` with a path throws a SecurityError. Every function here
 * no-ops unless the page is http(s), so the desktop app behaves exactly as it did.
 */

import type { View } from '../state/store'

/** view -> the path that names it. Anything absent stays addressless (see the note above). */
const PATHS: Partial<Record<View, string>> = {
  chat: '/',
  // NO `admin` ENTRY, and its absence is load-bearing. The admin console is its own DOCUMENT now
  // (ui/admin.html, served by nginx at /admin), so this app never renders that path and must not
  // claim it: pushing `/admin` into the address bar here would leave the chat shell showing at
  // the console's address, and a reload would then swap the page under the user. Desktop still
  // has an in-app `admin` view — it simply has no address, like every other view below.
  settings: '/settings',
  account: '/account',
  subscription: '/subscription',
  datasources: '/datasources',
  projects: '/projects',
  myagents: '/agents',
  marketplace: '/store'
}

const VIEWS: Record<string, View> = Object.fromEntries(
  Object.entries(PATHS).map(([view, path]) => [path, view as View])
) as Record<string, View>

/** Is this a page whose address bar we may touch? False in Electron (file://) and in tests. */
function addressable(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.history?.pushState === 'function' &&
    /^https?:$/.test(window.location.protocol)
  )
}

/** Strip a trailing slash so `/admin/` and `/admin` are one address. */
function normalize(pathname: string): string {
  const p = (pathname || '/').replace(/\/+$/, '')
  return p === '' ? '/' : p
}

/** The view this URL names, or null when it names none (unknown paths fall back to the default,
 *  which is what a stale bookmark or a typo should do rather than showing an empty shell). */
export function viewFromLocation(): View | null {
  if (!addressable()) return null
  return VIEWS[normalize(window.location.pathname)] || null
}

/** Put `view` in the address bar. A no-op for views with no address, so navigating to an agent
 *  page leaves the URL where it was instead of lying about where you are. */
export function pushView(view: View): void {
  if (!addressable()) return
  const path = PATHS[view]
  if (!path || normalize(window.location.pathname) === normalize(path)) return
  window.history.pushState({ view }, '', path + window.location.search)
}

/** Back/forward. Returns an unsubscribe so a caller can clean up. */
export function onRouteChange(handler: (view: View | null) => void): () => void {
  if (!addressable()) return () => {}
  const listener = (): void => handler(viewFromLocation())
  window.addEventListener('popstate', listener)
  return () => window.removeEventListener('popstate', listener)
}

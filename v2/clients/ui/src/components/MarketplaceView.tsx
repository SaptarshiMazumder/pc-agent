import { RefreshCw } from 'lucide-react'
import { useState } from 'react'

import { getSession } from '../lib/auth'
import { isDesktop } from '../lib/host'
import { useApp } from '../state/store'
import MarketplaceCards, { matches } from './MarketplaceCards'
import PageShell from './PageShell'
import SearchBox from './SearchBox'

/**
 * The Marketplace, INSIDE the app — the daemon-connected host of the shared card grid.
 *
 * Everything about how a card looks lives in MarketplaceCards, which the public marketplace page
 * renders too. What is here is the half only a daemon has: rows that know what this machine has
 * installed, the install/uninstall actions, and the viewer's identity.
 *
 * WHERE AN INSTALL LANDS depends on which daemon this client is attached to, and the two answers
 * are different enough that saying "this app" for both would be a lie.
 *
 * Desktop: the daemon runs on this machine, so an install is yours alone.
 * Browser: the daemon is a server, its agents directory is one shared disk (EFS on the hosted
 *   deployment), and per-user isolation does not exist yet — so an install is visible to, and
 *   removable by, everyone connected to it. That is the accepted trade for now, but a user
 *   clicking Uninstall deserves to know it is not only their copy going away.
 *
 * `isDesktop` is the Electron bridge's presence, so this is a fact about the host rather than a
 * guess about the deployment: a browser pointed at a local daemon during development still gets
 * the accurate reading ("this server", shared by whoever is connected to it).
 */
const SUBTITLE = isDesktop
  ? 'Install agents into this app — live, no restart. Or download a standalone installer, or open one in your browser.'
  : 'Install agents here — live, no restart. Download gives you a standalone app for your own machine; Open runs one in your browser with nothing to install.'

const INSTALL_TARGET = isDesktop ? 'this app' : 'this server — for everyone using it'

/** IDENTITY TRAVELS WITH THE LAUNCH — the same rule the desktop opener already follows. A bare
 * link makes the opened app page fall back to whatever session ITS OWN localStorage last stored,
 * which may be a different person than this shell is signed in as (found live: a freshly
 * signed-up user opened an app and was silently the machine's previous tester). `session=` is
 * the IDENTITY slot (never `token=`, the machine-secret slot); the SDK adopts it once into the
 * app's own storage and strips it from the address bar. No session (signed out) => the bare
 * link stands and the app's own sign-in gate takes over.
 *
 * The PUBLIC marketplace page passes no decorator at all: nobody is signed in there, so there is
 * no identity to carry and the bare link is the only honest one. */
export function webHref(webUrl: string): string {
  const s = getSession()
  if (!s?.token) return webUrl
  try {
    const u = new URL(webUrl)
    u.searchParams.set('session', s.token)
    return u.toString()
  } catch {
    return webUrl
  }
}

export default function MarketplaceView() {
  const catalog = useApp((s) => s.catalog)
  const catalogError = useApp((s) => s.catalogError)
  const installBusy = useApp((s) => s.installBusy)
  const installBundle = useApp((s) => s.installBundle)
  const uninstallBundle = useApp((s) => s.uninstallBundle)
  const refreshCatalog = useApp((s) => s.refreshCatalog)

  const [query, setQuery] = useState('')
  const bundles = catalog.filter((b) => matches(b, query))

  const actions = (
    <button className="btn" onClick={() => void refreshCatalog()} title="Re-read the registry index"><RefreshCw size={15} />Refresh</button>
  )
  const search = <SearchBox value={query} onChange={setQuery} placeholder="Search agents" />

  return (
    <PageShell title="Marketplace" sub={SUBTITLE} actions={actions} search={search}>
      {catalogError && <div className="banner banner-error">{catalogError}</div>}
      <MarketplaceCards
        bundles={bundles}
        busy={installBusy}
        onInstall={(id) => void installBundle(id)}
        onUninstall={(id) => void uninstallBundle(id)}
        installTarget={INSTALL_TARGET}
        webHref={webHref}
        filtered={query.trim() !== ''}
      />
    </PageShell>
  )
}

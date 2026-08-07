import { RefreshCw, Check } from 'lucide-react'
import { useState } from 'react'

import { glyph } from '../lib/glyphs'
import { isDesktop } from '../lib/platform'
import { useApp } from '../state/store'
import PageShell from './PageShell'
import SearchBox from './SearchBox'

/**
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
  ? 'Install agents into this app — live, no restart.'
  : 'Install agents on this server — live, no restart. Installs are shared by everyone using it.'

export default function StoreView() {
  const catalog = useApp((s) => s.catalog)
  const catalogError = useApp((s) => s.catalogError)
  const installBusy = useApp((s) => s.installBusy)
  const installBundle = useApp((s) => s.installBundle)
  const uninstallBundle = useApp((s) => s.uninstallBundle)
  const refreshCatalog = useApp((s) => s.refreshCatalog)

  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const bundles = catalog.filter((b) => !q || b.name.toLowerCase().includes(q) || (b.description || '').toLowerCase().includes(q))

  const actions = (
    <button className="btn" onClick={() => void refreshCatalog()}><RefreshCw size={15} />Refresh</button>
  )
  const search = <SearchBox value={query} onChange={setQuery} placeholder="Search agents" />

  return (
    <PageShell title="Store" sub={SUBTITLE} actions={actions} search={search}>
        {catalogError && <div className="banner banner-error">{catalogError}</div>}

        <div className="cards">
          {bundles.map((b) => {
            const busy = installBusy[b.id]
            const paid = b.price && b.price !== 'free'
            return (
              <div className="card" key={b.id}>
                <div className="card-top">
                  <span className="card-icon">{glyph(b.icon, 20)}</span>
                  <div className="grow">
                    <div className="card-name">{b.name}</div>
                    <div className="card-by">{b.entitlement ? 'licensed' : 'agentd'}</div>
                  </div>
                </div>
                <div className="badges">
                  <span className="badge">{b.version}</span>
                  <span className={`badge ${paid ? 'paid' : 'free'}`}>{b.price || 'free'}</span>
                  {b.installed && <span className="badge ok"><Check size={11} />installed</span>}
                  {b.updateAvailable && <span className="badge update">update</span>}
                  {!b.compatible && <span className="badge paid">needs newer agentd</span>}
                </div>
                <p className="card-desc">{b.description || 'No description.'}</p>
                <div className="card-actions">
                  {busy ? (
                    <span className="page-sub">{busy}</span>
                  ) : b.installed && !b.updateAvailable ? (
                    <button className="btn ghost" onClick={() => void uninstallBundle(b.id)}>Uninstall</button>
                  ) : (
                    <button className="btn primary" disabled={!b.compatible} onClick={() => void installBundle(b.id)}>
                      {b.updateAvailable ? `Update to ${b.version}` : paid ? `Get — ${b.price}` : 'Install'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
          {/* Two different nothings. "No bundles match" over an EMPTY registry sends you hunting
              for a typo in a search box you never typed in — say which one it is. */}
          {bundles.length === 0 && !catalogError && (
            <div className="page-sub">
              {q ? 'No bundles match.' : 'Nothing published to this registry yet.'}
            </div>
          )}
        </div>
    </PageShell>
  )
}

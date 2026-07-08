import { RefreshCw, Search, Check } from 'lucide-react'
import { useState } from 'react'

import { glyph } from '../lib/glyphs'
import { useApp } from '../state/store'

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

  return (
    <div className="store">
      <div className="store-inner">
        <div className="page-head">
          <div className="grow">
            <div className="page-title">Store</div>
            <div className="page-sub">Install agents into this app — live, no restart.</div>
          </div>
          <div className="pill-search">
            <Search size={16} />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search agents" />
          </div>
          <button className="btn" onClick={() => void refreshCatalog()}><RefreshCw size={15} />Refresh</button>
        </div>

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
          {bundles.length === 0 && !catalogError && <div className="page-sub">No bundles match.</div>}
        </div>
      </div>
    </div>
  )
}

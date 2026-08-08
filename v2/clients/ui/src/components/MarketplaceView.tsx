import { RefreshCw, Check, Download } from 'lucide-react'
import { useState } from 'react'

import { glyph } from '../lib/glyphs'
import { hostOs, isDesktop } from '../lib/platform'
import { useApp } from '../state/store'
import PageShell from './PageShell'
import SearchBox from './SearchBox'

/**
 * The Marketplace (formerly "Store").
 *
 * TWO WAYS TO GET AN AGENT, and they are not the same thing:
 *
 *   Install   — the daemon this client is attached to unpacks the .agentpkg. Requires a daemon,
 *               so it only ever reaches someone who already runs the product.
 *   Download  — a standalone installer for your own machine. This is the one that works for a
 *               person who has nothing installed yet, which is most of the market.
 *
 * Download appears only when the publisher actually shipped an installer for the viewer's OS
 * (`installers` on the catalog row). Nothing here knows which agents have one — the registry
 * index says, and an agent that ships none simply has no button.
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
  ? 'Install agents into this app — live, no restart. Or download a standalone installer.'
  : 'Install agents on this server — live, no restart. Installs are shared by everyone using it. Download gives you a standalone app for your own machine.'

const OS_LABEL: Record<string, string> = { win: 'Windows', mac: 'macOS', linux: 'Linux' }

/** Installer sizes are hundreds of megabytes — worth stating before someone clicks. */
function humanSize(bytes: number): string {
  if (!bytes || bytes < 0) return ''
  const mb = bytes / 1048576
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`
}

export default function MarketplaceView() {
  const catalog = useApp((s) => s.catalog)
  const catalogError = useApp((s) => s.catalogError)
  const installBusy = useApp((s) => s.installBusy)
  const installBundle = useApp((s) => s.installBundle)
  const uninstallBundle = useApp((s) => s.uninstallBundle)
  const refreshCatalog = useApp((s) => s.refreshCatalog)

  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const bundles = catalog.filter((b) => !q || b.name.toLowerCase().includes(q) || (b.description || '').toLowerCase().includes(q))
  const os = hostOs()

  const actions = (
    <button className="btn" onClick={() => void refreshCatalog()} title="Re-read the registry index"><RefreshCw size={15} />Refresh</button>
  )
  const search = <SearchBox value={query} onChange={setQuery} placeholder="Search agents" />

  return (
    <PageShell title="Marketplace" sub={SUBTITLE} actions={actions} search={search}>
        {catalogError && <div className="banner banner-error">{catalogError}</div>}

        <div className="cards">
          {bundles.map((b) => {
            const busy = installBusy[b.id]
            const paid = b.price && b.price !== 'free'
            // The publisher may ship installers for other platforms; only ours is offerable.
            const installer = (b.installers || []).find((a) => a.platform === os)
            const size = installer ? humanSize(installer.size) : ''
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
                    <button className="btn ghost" onClick={() => void uninstallBundle(b.id)} title={isDesktop ? `Remove ${b.name} from this app` : `Remove ${b.name} from this server — for everyone using it`}>Uninstall</button>
                  ) : (
                    <button className="btn primary" disabled={!b.compatible} onClick={() => void installBundle(b.id)} title={b.compatible ? `Install ${b.name} ${b.version} ${isDesktop ? 'into this app' : 'on this server'}` : `${b.name} needs a newer agentd than this one`}>
                      {b.updateAvailable ? `Update to ${b.version}` : paid ? `Get — ${b.price}` : 'Install'}
                    </button>
                  )}
                  {installer && (
                    <a
                      className="btn ghost"
                      href={installer.url}
                      download
                      title={`Download the standalone ${OS_LABEL[os] || os} installer for ${b.name} ${b.version}${size ? ` (${size})` : ''} — runs on your own machine, no agentd needed`}
                    >
                      <Download size={15} />
                      {size ? `Download · ${size}` : 'Download'}
                    </a>
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

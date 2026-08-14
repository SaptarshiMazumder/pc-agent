import { RefreshCw, Check, Download, Globe } from 'lucide-react'
import { useState } from 'react'

import type { CatalogBundle } from '../gateway/protocol'
import { getSession } from '../lib/auth'
import { glyph } from '../lib/glyphs'
import { hostOs, isDesktop } from '../lib/platform'
import { useApp } from '../state/store'
import PageShell from './PageShell'
import SearchBox from './SearchBox'

/**
 * The Marketplace (formerly "Store").
 *
 * THREE WAYS TO GET AN AGENT, and they are not the same thing:
 *
 *   Install   — the daemon this client is attached to unpacks the .agentpkg. Requires a daemon,
 *               so it only ever reaches someone who already runs the product.
 *   Download  — a standalone installer for your own machine. Works for a person who has nothing
 *               installed yet, which is most of the market.
 *   Open      — the agent runs on the HOSTED deployment; the button is just a link. Works for a
 *               person who will never install anything, which is most of the internet.
 *
 * Download appears only when the publisher actually shipped an installer for the viewer's OS
 * (`installers` on the catalog row); Open only when the author declared web delivery and the
 * registry names a hosted deployment (`webUrl`, joined daemon-side). Nothing here knows which
 * agents offer what — the registry index says, and a card renders exactly the doors its author
 * opened.
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

const OS_LABEL: Record<string, string> = { win: 'Windows', mac: 'macOS', linux: 'Linux' }

/** IDENTITY TRAVELS WITH THE LAUNCH — the same rule the desktop opener already follows. A bare
 * link makes the opened app page fall back to whatever session ITS OWN localStorage last stored,
 * which may be a different person than this shell is signed in as (found live: a freshly
 * signed-up user opened an app and was silently the machine's previous tester). `session=` is
 * the IDENTITY slot (never `token=`, the machine-secret slot); the SDK adopts it once into the
 * app's own storage and strips it from the address bar. No session (signed out) => the bare
 * link stands and the app's own sign-in gate takes over. */
function webHref(webUrl: string): string {
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

/**
 * The line under an agent's name: WHO published it and WHICH agent it is.
 *
 * Both, because neither is enough on its own. A display name is chosen by its owner and is not
 * unique — two creators may both call themselves "Bio Labs" — while the id is unique, is what
 * `agentd install <id>` takes, and is what the entry's signature was actually verified against.
 *
 * The publisher is never guessed here. The daemon resolves the creator id against the registry's
 * SIGNED roster and sends the name it found; an unnamed publisher falls back to the raw creator
 * id rather than to a friendly word, because "agentd" under a stranger's agent is a claim this
 * client cannot make.
 */
function byline(b: CatalogBundle): string {
  const who = (b.publisher || b.publisherId || '').trim()
  return who ? `by ${who} · ${b.id}` : b.id
}

function bylineTitle(b: CatalogBundle): string {
  const parts = [`Agent id: ${b.id}`]
  if (b.publisher) parts.push(`Published by ${b.publisher}`)
  if (b.publisherId) parts.push(`Publisher id: ${b.publisherId}`)
  if (!b.publisher && !b.publisherId) parts.push('This registry does not name a publisher')
  return parts.join(' — ')
}

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
  // Searchable by everything a card SHOWS. The id and the publisher are on every card now, and a
  // search box that ignores what the reader is looking at reads as broken.
  const bundles = catalog.filter((b) =>
    !q || [b.name, b.description, b.id, b.publisher, b.publisherId].some((f) => (f || '').toLowerCase().includes(q))
  )
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
                    <div className="card-name" title={b.name}>{b.name}</div>
                    <div className="card-by" title={bylineTitle(b)}>{byline(b)}</div>
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
                  {b.webUrl && (
                    <a
                      className="btn ghost"
                      href={webHref(b.webUrl)}
                      target="_blank"
                      rel="noreferrer"
                      title={`Open ${b.name} in your browser — runs hosted, nothing to install`}
                    >
                      <Globe size={15} />
                      Open in browser
                    </a>
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

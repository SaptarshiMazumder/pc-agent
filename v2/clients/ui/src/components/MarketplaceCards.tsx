import { Check, Download, Globe } from 'lucide-react'

import type { CatalogBundle } from '../gateway/protocol'
import { glyph } from '../lib/glyphs'
import { hostOs } from '../lib/host'

/**
 * The marketplace card grid — the ONE implementation of what a store card is.
 *
 * PRESENTATIONAL ON PURPOSE. It reads no store, opens no socket and imports nothing that does, so
 * the same component renders in two places that have almost nothing in common:
 *
 *   inside the app        rows from the daemon's `marketplace.catalog`, with Install/Uninstall
 *   the public page       rows from a static catalog.json, with no daemon anywhere
 *
 * That split is the whole reason the marketplace can exist as its own page. A card is not "the
 * app's store"; it is a listing, and the DOORS it renders are whatever this deployment can offer.
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
 * registry names a hosted deployment (`webUrl`, joined registry-side). Nothing here knows which
 * agents offer what — the registry says, and a card renders exactly the doors its author opened.
 * Install appears only when a caller passed handlers for it, which is how a page with no daemon
 * renders the same grid without pretending it can install anything.
 */

const OS_LABEL: Record<string, string> = { win: 'Windows', mac: 'macOS', linux: 'Linux' }

/**
 * The line under an agent's name: WHO published it and WHICH agent it is.
 *
 * Both, because neither is enough on its own. A display name is chosen by its owner and is not
 * unique — two creators may both call themselves "Bio Labs" — while the id is unique, is what
 * `agentd install <id>` takes, and is what the entry's signature was actually verified against.
 *
 * The publisher is never guessed here. The registry resolves the creator id against its SIGNED
 * roster and the row carries the name it found; an unnamed publisher falls back to the raw creator
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

/**
 * Why this agent has no button for this visitor — the two answers being genuinely different.
 *
 * The author shipped installers but none for this OS: a Linux visitor looking at a Windows-only
 * agent is not looking at a broken listing, and telling them so is the difference between "come
 * back on another machine" and "this store does not work".
 *
 * The author shipped no standalone build at all: it is a bundle, which means it installs INTO
 * agentd and the way to get it is to run agentd.
 */
function unavailable(b: CatalogBundle, os: string): string {
  if (b.installers?.length) {
    return `No download for ${OS_LABEL[os] || 'your system'} — this agent ships for ${b.installers
      .map((a) => OS_LABEL[a.platform] || a.platform)
      .join(', ')}.`
  }
  return 'Available inside the agentd app — install it from the app’s Marketplace.'
}

/** Does this row match a search? Everything a card SHOWS is searchable — a box that ignores what
 *  the reader is looking at reads as broken. Exported so both hosts filter identically. */
export function matches(b: CatalogBundle, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [b.name, b.description, b.id, b.publisher, b.publisherId].some((f) =>
    (f || '').toLowerCase().includes(q)
  )
}

export interface MarketplaceCardsProps {
  bundles: CatalogBundle[]
  /** per-bundle progress text ('installing…'); a busy card shows it instead of its buttons */
  busy?: Record<string, string>
  /** omit BOTH to render a listing with no install door — the public page's shape */
  onInstall?: (id: string) => void
  onUninstall?: (id: string) => void
  /** where an install lands, for the button's tooltip. Ignored without `onInstall`. */
  installTarget?: string
  /** decorate an Open-in-browser link (the app carries the viewer's identity across; see
   *  MarketplaceView). Identity is never invented here. */
  webHref?: (url: string) => string
  /** shown when there are no rows AND no search is active */
  emptyText?: string
  /** true when a search is narrowing the list, so "nothing here" can say which nothing it is */
  filtered?: boolean
}

export default function MarketplaceCards({
  bundles,
  busy = {},
  onInstall,
  onUninstall,
  installTarget = 'this app',
  webHref = (url) => url,
  emptyText = 'Nothing published to this registry yet.',
  filtered = false
}: MarketplaceCardsProps) {
  const os = hostOs()

  return (
    <div className="cards">
      {bundles.map((b) => {
        const working = busy[b.id]
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
              {onInstall && !b.compatible && <span className="badge paid">needs newer agentd</span>}
            </div>
            <p className="card-desc">{b.description || 'No description.'}</p>
            <div className="card-actions">
              {working ? (
                <span className="page-sub">{working}</span>
              ) : onInstall && b.installed && !b.updateAvailable ? (
                <button
                  className="btn ghost"
                  onClick={() => onUninstall?.(b.id)}
                  title={`Remove ${b.name} from ${installTarget}`}
                >
                  Uninstall
                </button>
              ) : onInstall ? (
                <button
                  className="btn primary"
                  disabled={!b.compatible}
                  onClick={() => onInstall(b.id)}
                  title={
                    b.compatible
                      ? `Install ${b.name} ${b.version} into ${installTarget}`
                      : `${b.name} needs a newer agentd than this one`
                  }
                >
                  {b.updateAvailable ? `Update to ${b.version}` : paid ? `Get — ${b.price}` : 'Install'}
                </button>
              ) : null}
              {b.webUrl && (
                <a
                  className={`btn ${onInstall ? 'ghost' : 'primary'}`}
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
              {/* A card with NO door at all. It happens on the public page whenever an author
                  offers neither web delivery nor an installer this visitor's OS can run — and a
                  listing that just stops, with no button and no explanation, reads as broken
                  rather than as unavailable. Say which of the two it is. */}
              {!working && !onInstall && !b.webUrl && !installer && (
                <span className="page-sub">{unavailable(b, os)}</span>
              )}
            </div>
          </div>
        )
      })}
      {/* Two different nothings. "No bundles match" over an EMPTY registry sends you hunting
          for a typo in a search box you never typed in — say which one it is. */}
      {bundles.length === 0 && (
        <div className="page-sub">{filtered ? 'No bundles match.' : emptyText}</div>
      )}
    </div>
  )
}

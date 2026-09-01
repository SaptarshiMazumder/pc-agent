import { RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import MarketplaceCards, { matches } from '@ui/components/MarketplaceCards'
import PageShell from '@ui/components/PageShell'
import SearchBox from '@ui/components/SearchBox'
import type { CatalogBundle } from '@ui/gateway/protocol'
import { assetUrl, fetchCatalog, type CatalogDoc } from '@ui/lib/catalog'
import { installSoftScroll } from '@ui/lib/softScroll'

/**
 * The public marketplace.
 *
 * WHAT MAKES THIS PAGE DIFFERENT from the store inside the app: there is no daemon here, so
 * "Install" is not a door it can open. What it offers is the two doors that work for a person who
 * has never heard of agentd — Open in browser (the agent runs on the hosted deployment) and
 * Download (a standalone installer for their own machine). Both are plain links, which is why
 * this whole page is a static file and not a service.
 *
 * WHERE THE CATALOG COMES FROM, in falling order of specificity:
 *
 *   ?catalog=<url>              a reader pointing this page at another registry
 *   VITE_AGENTD_CATALOG_URL     baked at build time, for a site serving one known registry
 *   ./catalog.json              the default: same origin as the page
 *
 * The default is same-origin on purpose. It is what makes the deployment need no configuration
 * and no CORS: one distribution serves the page from the site bucket and /catalog.json from the
 * registry bucket, and the browser never knows they are two different places.
 */
function catalogUrl(): string {
  const asked = new URLSearchParams(location.search).get('catalog')
  if (asked) return asked
  const baked = import.meta.env.VITE_AGENTD_CATALOG_URL as string | undefined
  return baked || new URL('catalog.json', location.href).toString()
}

const SUBTITLE =
  'Open one in your browser with nothing to install, or download a standalone app for your own machine.'

export default function App() {
  const [doc, setDoc] = useState<CatalogDoc | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setDoc(await fetchCatalog(catalogUrl()))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // The app-wide scroll-edge fade, so a listing here feels like a listing there.
  useEffect(() => installSoftScroll(), [])

  /**
   * Artifact urls, joined against what the registry said its base was.
   *
   * Rows are relative when the registry is a plain directory and absolute when the publish
   * service knew the public base — `assetUrl` handles both, and falls back to the catalog's own
   * address. Done HERE rather than in the shared card grid because it is a fact about how this
   * page fetched its data, and the in-app store's rows arrive already joined by the daemon.
   */
  const bundles: CatalogBundle[] = (doc?.bundles || [])
    .filter((b) => matches(b, query))
    .map((b) =>
      b.installers?.length
        ? { ...b, installers: b.installers.map((a) => ({ ...a, url: assetUrl(doc!, a.url, catalogUrl()) })) }
        : b
    )

  const actions = (
    <button className="btn" onClick={() => void load()} title="Re-read the registry">
      <RefreshCw size={15} />
      Refresh
    </button>
  )
  const search = <SearchBox value={query} onChange={setQuery} placeholder="Search agents" />

  return (
    <PageShell title={doc?.registry || 'Marketplace'} sub={SUBTITLE} actions={actions} search={search}>
      {error && <div className="banner banner-error">{error}</div>}
      {loading && !doc ? (
        <div className="page-sub">Loading the marketplace…</div>
      ) : (
        <MarketplaceCards
          bundles={bundles}
          emptyText="Nothing has been published to this marketplace yet."
          filtered={query.trim() !== ''}
        />
      )}
    </PageShell>
  )
}

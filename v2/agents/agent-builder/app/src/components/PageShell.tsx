import type { ReactNode } from 'react'

/**
 * The one page scaffold every non-chat page uses.
 *
 * It renders a FIXED header band (title + subtitle + actions) and, optionally, a FIXED left nav
 * rail — with ONLY the body scrolling. It's a flex column: the header and nav are `flex:none`
 * siblings and the body is the sole `overflow:auto` region, so they stay pinned WITHOUT
 * `position:sticky` (which would suppress the app-wide soft scroll-edge fade on the body — see
 * lib/softScroll.ts). Same technique the entity pages (.entity-page) already use.
 *
 * Nothing here is page-specific — any page, current or future, gets the pinned-header /
 * scrolling-body behaviour for free just by rendering:
 *
 *   <PageShell title="…" sub="…" actions={…}>…scrollable content…</PageShell>
 *
 * Pass a `nav` to get the two-column (Settings-style) layout with a pinned rail. Pass a `search`
 * node to get a pinned search bar between the header and the scrolling body — it never scrolls
 * away, and (since it lives in the scaffold, not the page) every page gets it the same way.
 */
export default function PageShell({
  title,
  sub,
  actions,
  nav,
  search,
  children,
  wide = true,
  className = ''
}: {
  title: ReactNode
  sub?: ReactNode
  actions?: ReactNode
  /** optional left rail; when given, the body becomes a two-column pinned-nav layout */
  nav?: ReactNode
  /** optional pinned search bar, shown below the header band and above the scrolling body */
  search?: ReactNode
  children: ReactNode
  wide?: boolean
  className?: string
}) {
  const inner = `settings-inner${wide ? ' settings-wide' : ''}`
  return (
    <div className={`settings ${className}`.trim()}>
      {/* pinned top band — never scrolls */}
      <div className="page-bar">
        <div className={inner}>
          <div className="settings-head">
            <div className="settings-head-titles">
              <div className="page-title">{title}</div>
              {sub != null && sub !== '' && <div className="page-sub">{sub}</div>}
            </div>
            {actions != null && <div className="settings-head-actions">{actions}</div>}
          </div>
        </div>
      </div>

      {nav != null ? (
        // two-column: pinned nav rail + a content column that pins its own search over a
        // scrolling body (only .settings-content scrolls)
        <div className="settings-layout">
          <nav className="settings-nav">{nav}</nav>
          <div className="settings-col">
            {search != null && <div className="page-search page-search--col">{search}</div>}
            <div className="settings-content">{children}</div>
          </div>
        </div>
      ) : (
        // single-column: pinned search (optional) over a scrolling body
        <>
          {search != null && (
            <div className="page-search">
              <div className={inner}>{search}</div>
            </div>
          )}
          <div className="settings-body">
            <div className={inner}>{children}</div>
          </div>
        </>
      )}
    </div>
  )
}

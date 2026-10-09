/* ChatMenu — the per-chat "⋯" menu in the rail: Rename, Duplicate, and Delete (danger, below a
 * separator only when something sits above it). It PORTALS to document.body with
 * position:fixed and places itself right-aligned under the ⋯ it was opened from, flipping
 * above it near the bottom of the window.
 *
 * Each cell renders the real rail rows (SessionItem) and opens the menu against the measured
 * rect of a row's ⋯ position — the same DOMRect SessionItem hands it on click. Because the menu
 * is fixed to the window, this card needs single-story mode (one menu per page).
 *
 * Cells: the full menu; a window that cannot fork (no Duplicate); delete-only (no separator);
 * and the last row near the window's bottom edge, where the menu flips above. The armed
 * "Click again to delete" label follows a first click and cannot be shown statically. */
import { ChatMenu, SessionItem } from 'agent-app'
import { useLayoutEffect, useRef, useState } from 'react'

const now = Math.floor(Date.now() / 1000)
const noop = () => {}

const CHATS = [
  { sessionId: 's1', title: 'Rooftop portrait at golden hour, 1024×1536', messages: 14, modified: now - 300, running: true },
  { sessionId: 's2', title: 'Product ad — ceramic mug on linen', messages: 22, modified: now - 7200 },
  { sessionId: 's3', title: 'Lip-sync test with the singing clip', messages: 9, modified: now - 86400 * 2 },
  { sessionId: 's4', title: 'Anime key visual, rainy neon street', messages: 6, modified: now - 86400 * 4 },
]

function Opened({
  row,
  spacer = 0,
  items,
}: {
  row: number
  spacer?: number
  items: { onRename?: () => void; onDuplicate?: () => void; onDelete?: () => void }
}) {
  const rail = useRef<HTMLDivElement>(null)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  useLayoutEffect(() => {
    const el = rail.current?.querySelectorAll('.session-row')[row] as HTMLElement | undefined
    if (!el) return
    const r = el.getBoundingClientRect()
    // Where the row's hover-revealed ⋯ sits: a 24px square at its right edge.
    setAnchor(new DOMRect(r.right - 30, r.top + (r.height - 24) / 2, 24, 24))
  }, [row])
  return (
    // minHeight keeps the dark surface under the menu, which hangs below the rail's last row.
    <div ref={rail} style={{ width: 260, minHeight: 300, paddingTop: spacer, display: 'flex', flexDirection: 'column', gap: 2 }}>
      {CHATS.map((c, i) => (
        <SessionItem key={c.sessionId} session={c} active={i === row} onOpen={noop} onRename={noop} onDelete={noop} />
      ))}
      {anchor && <ChatMenu anchor={anchor} onClose={noop} {...items} />}
    </div>
  )
}

export const FullMenu = () => <Opened row={0} items={{ onRename: noop, onDuplicate: noop, onDelete: noop }} />

export const NoDuplicate = () => <Opened row={1} items={{ onRename: noop, onDelete: noop }} />

export const DeleteOnly = () => <Opened row={2} items={{ onDelete: noop }} />

export const FlipsAboveNearBottom = () => (
  // Pushes the rail down so its last row sits ~40px above the window's bottom edge, whatever
  // the card's viewport — the menu no longer fits below and opens above the ⋯ instead.
  <Opened row={3} spacer={Math.max(0, window.innerHeight - 230)} items={{ onRename: noop, onDuplicate: noop, onDelete: noop }} />
)

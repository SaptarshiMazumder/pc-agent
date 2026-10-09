/* SessionItem — one saved-conversation row in the sidebar: one ellipsised line, a live dot while
 * a run is going, and a "⋯" menu (shown on hover) for rename / duplicate / delete. Rendered in a
 * rail-width column, the way the Sidebar lists them.
 *
 * Hover-only affordances (the ⋯ button, the delete spinner — it sits in the hover-revealed
 * actions strip) cannot render statically, so the cells sweep what IS visible at rest: the
 * active row, the live-run dot, ellipsis on long titles, and the untitled fallback. */
import { SessionItem } from 'agent-app'

const now = Math.floor(Date.now() / 1000)
const noop = () => {}

const Rail = ({ children }: { children: React.ReactNode }) => (
  <div style={{ width: 260, padding: 8, display: 'flex', flexDirection: 'column', gap: 2 }}>{children}</div>
)

export const List = () => (
  <Rail>
    <SessionItem
      session={{ sessionId: 's1', title: 'Rooftop portrait at golden hour, 1024×1536', messages: 14, modified: now - 300, running: true }}
      active
      onOpen={noop}
      onRename={noop}
      onDuplicate={noop}
      onDelete={noop}
    />
    <SessionItem
      session={{ sessionId: 's2', title: 'Product ad — ceramic mug on linen', messages: 22, modified: now - 7200 }}
      active={false}
      onOpen={noop}
      onRename={noop}
      onDelete={noop}
    />
    <SessionItem
      session={{ sessionId: 's3', title: 'Lip-sync test with the singing clip and a slow push-in on her face', messages: 9, modified: now - 86400 * 2 }}
      active={false}
      onOpen={noop}
      onRename={noop}
      onDelete={noop}
    />
    <SessionItem session={{ sessionId: 's4', messages: 2, modified: now - 86400 * 9 }} active={false} onOpen={noop} />
  </Rail>
)

export const ActiveIdle = () => (
  <Rail>
    <SessionItem
      session={{ sessionId: 's5', title: 'Anime key visual, rainy neon street', messages: 6, modified: now - 600 }}
      active
      onOpen={noop}
      onRename={noop}
      onDelete={noop}
    />
  </Rail>
)

export const RunningInBackground = () => (
  <Rail>
    <SessionItem
      session={{ sessionId: 's6', title: 'Wan 2.2 turntable of the sneaker, 81 frames', messages: 11, modified: now - 60, running: true }}
      active={false}
      onOpen={noop}
      onRename={noop}
      onDelete={noop}
    />
  </Rail>
)

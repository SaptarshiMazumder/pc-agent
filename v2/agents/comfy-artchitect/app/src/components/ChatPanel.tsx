/* The conversation column, beside the studio: its head (the chat's name, the newest workflow, the
 * context meter, swap side, fold), the thread or the opening, and the foot (installs, background
 * jobs, the composer).
 *
 * IT FOLDS to a strip at the window's edge so the studio can have the whole width — never gone:
 * the strip is always there, and one click brings the chat back exactly as it was. */

import { MessageSquareText, PanelLeft, PanelRight, PanelRightClose } from 'lucide-react'
import type { ReactNode } from 'react'

import { useApp } from '../state/store'

export function ChatPanel({
  title,
  sub,
  pct,
  folded,
  body,
  foot,
}: {
  title: string
  /** The newest workflow's file name, under the title. */
  sub: string
  /** How full the context window is, 0-100, or null when the daemon has not said. */
  pct: number | null
  /** Folded to the strip (App decides: the person's fold, on a viewport wide enough to fold). */
  folded: boolean
  body: ReactNode
  foot: ReactNode
}) {
  const chatSide = useApp((s) => s.chatSide)
  const setChatSide = useApp((s) => s.setChatSide)
  const chatWidth = useApp((s) => s.chatWidth)
  const toggle = useApp((s) => s.toggleChat)

  if (folded)
    return (
      <button type="button" className="st-convo-strip" onClick={toggle} title="Open the chat" aria-label="Open the chat">
        <MessageSquareText size={17} strokeWidth={1.8} />
        <span className="st-convo-strip-label">Penguin</span>
      </button>
    )

  return (
    /* Width is INLINE because it is user state, not design state — the stylesheet owns the
       minimum, this owns what the person dragged it to. */
    <div className="st-convo" style={{ width: chatWidth }}>
      <div className="st-convo-head">
        <span className="st-live-dot" />
        <div className="st-convo-titles">
          <span className="st-convo-title">{title}</span>
          {sub && <span className="st-convo-sub st-mono">{sub}</span>}
        </div>
        {pct !== null && <span className="st-ctx-pill st-mono">{pct}% ctx</span>}
        <button
          type="button"
          className="st-swap"
          title="Swap chat side"
          onClick={() => setChatSide(chatSide === 'left' ? 'right' : 'left')}
        >
          {chatSide === 'left' ? <PanelRight size={14} strokeWidth={1.7} /> : <PanelLeft size={14} strokeWidth={1.7} />}
        </button>
        <button type="button" className="st-swap st-fold" title="Fold the chat away" aria-label="Fold the chat away" onClick={toggle}>
          <PanelRightClose size={14} strokeWidth={1.7} />
        </button>
      </div>
      <div className="st-convo-body">{body}</div>
      <div className="st-convo-foot">{foot}</div>
    </div>
  )
}

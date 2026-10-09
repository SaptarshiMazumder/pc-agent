/* The chat column, on the studio's right: its title and context meter, the conversation (or a new
 * chat's opening), and the composer.
 *
 * It FOLDS to a strip at the window's edge so the studio can have the whole width — never gone: the
 * strip is always there, and one click brings the chat back exactly as it was. */

import { MessageSquareText, PanelRightClose } from 'lucide-react'
import type { ReactNode } from 'react'

import { useApp } from '../state/store'

export function ChatPanel({
  title,
  meter,
  body,
  composer,
}: {
  title: string
  /** The context meter, when the daemon has reported one. */
  meter: ReactNode
  /** The thread, or a new chat's opening. */
  body: ReactNode
  composer: ReactNode
}) {
  const width = useApp((s) => s.chatWidth)
  const collapsed = useApp((s) => s.chatCollapsed)
  const toggle = useApp((s) => s.toggleChat)

  if (collapsed)
    return (
      <button className="convo-strip" onClick={toggle} title="Open the chat" aria-label="Open the chat">
        <MessageSquareText size={17} />
        <span className="convo-strip-label">Agent</span>
      </button>
    )

  return (
    <section className="st-convo" style={{ width }}>
      <header className="convo-head">
        <h1 className="convo-title">{title}</h1>
        {meter}
        <button className="convo-fold" onClick={toggle} title="Fold the chat away" aria-label="Fold the chat away">
          <PanelRightClose size={15} />
        </button>
      </header>
      {body}
      {composer}
    </section>
  )
}

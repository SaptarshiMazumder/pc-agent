/* The conversation, in the order it happened.
 *
 * Autoscroll FOLLOWS THE USER: the moment you scroll away from the bottom it stops, and it
 * resumes when you come back. A chat that yanks you to the bottom mid-read is unusable.
 *
 * Tool activity is shown as it happens — that is how you watch an agent being built.
 */

import { useEffect, useRef } from 'react'
import type { ThreadItem } from '../agentd/chat'
import type { AgentRow } from '../agentd/roster'
import { renderMarkdown } from '../markdown/md'
import { Hero } from './Hero'

export function Thread({
  items,
  agents,
  onOpenAgent,
  onSuggest,
}: {
  items: ThreadItem[]
  agents: AgentRow[]
  onOpenAgent: (id: string) => void
  onSuggest: (text: string) => void
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const el = boxRef.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [items])

  return (
    <div className="thread" ref={boxRef}>
      {items.length === 0 ? (
        <Hero agents={agents} onOpenAgent={onOpenAgent} onSuggest={onSuggest} />
      ) : (
        items.map((item, i) => <Item key={i} item={item} />)
      )}
    </div>
  )
}

function Item({ item }: { item: ThreadItem }) {
  switch (item.kind) {
    case 'scope':
      return (
        <div className="scope-row">
          <span className="scope-dot" />
          Working on <b>{item.name}</b>
          <span className="scope-path">agents/{item.id}/</span>
        </div>
      )

    case 'user':
      return (
        <div className="msg user">
          <div className="role">You</div>
          <div className="bubble">
            {item.text}
            {item.files.length > 0 && (
              // What the user just attached, shown on their own bubble — an image as a thumbnail
              // so they can see WHICH screenshot they sent, anything else as a named chip.
              <div className="msg-files">
                {item.files.map((a, i) =>
                  a.mimeType?.startsWith('image/') ? (
                    <img
                      key={i}
                      src={`data:${a.mimeType};base64,${a.dataBase64}`}
                      alt={a.name}
                      title={a.name}
                    />
                  ) : (
                    <span className="chip-file" key={i}>
                      {a.name}
                    </span>
                  ),
                )}
              </div>
            )}
          </div>
        </div>
      )

    case 'bot':
      return (
        <div className="msg bot">
          <div className="role">Agent Builder</div>
          {/* The renderer escapes every span of source before introducing a tag, so the only
              markup that reaches here is its own — see markdown/md.ts. */}
          <div
            className="bubble md"
            dangerouslySetInnerHTML={{
              __html: renderMarkdown(item.text) + (item.streaming ? '<span class="caret"></span>' : ''),
            }}
          />
        </div>
      )

    case 'think':
      return <div className="think">{item.text}</div>

    case 'tool':
      return (
        <div className={`tool ${item.error ? 'err' : ''}`}>
          {item.done ? (
            <span className={item.error ? 'fail' : 'done'}>{item.error ? '✕' : '✓'}</span>
          ) : (
            <span className="spin" />
          )}
          <span className="tname">{item.name}</span>
          <span className="targs">{item.args}</span>
        </div>
      )

    case 'fallback':
      return (
        <div className="tool err">
          <span className="fail">⚠</span>
          <span className="tname">{item.from} unavailable</span>
          <span className="targs">
            → {item.to} · {item.reason}
          </span>
        </div>
      )
  }
}

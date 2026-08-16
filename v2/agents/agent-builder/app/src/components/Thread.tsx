/* The conversation, in the order it happened.
 *
 * Autoscroll FOLLOWS THE USER: the moment you scroll away from the bottom it stops, and it
 * resumes when you come back. A chat that yanks you to the bottom mid-read is unusable.
 *
 * Tool activity is shown as it happens — that is how you watch an agent being built.
 */

import { useEffect, useRef, useState } from 'react'
import type { ThinkItem, ThreadItem } from '../agentd/chat'
import type { AgentRow } from '../agentd/roster'
import { renderMarkdown } from '../markdown/md'
import { Hero } from './Hero'
import { Thinking } from './Thinking'

export function Thread({
  items,
  running,
  agents,
  onOpenAgent,
  onSuggest,
}: {
  items: ThreadItem[]
  running: boolean
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
  }, [items, running])

  // Shown while the run has nothing to say YET — before the first token, and through every tool
  // call. Once prose is streaming the caret is already proof of life, and a second indicator under
  // it would just be noise.
  const last = items[items.length - 1]
  const streamingProse = last?.kind === 'bot' && last.streaming
  const working = running && !streamingProse

  return (
    <div className="thread" ref={boxRef}>
      <div className="thread-inner">
        {items.length === 0 ? (
          <Hero agents={agents} onOpenAgent={onOpenAgent} onSuggest={onSuggest} />
        ) : (
          items.map((item, i) => <Item key={i} item={item} />)
        )}
        {working && <Thinking />}
      </div>
    </div>
  )
}

/* Reasoning, CONTAINED.
 *
 * While it streams it is a fixed-height box that scrolls itself and follows the text. That height
 * is the whole point: reasoning can run for minutes, and left to grow it buries the answer the
 * user actually came for under a wall of workings.
 *
 * When it ends it folds to one line with its duration, expandable. Nothing is thrown away — it is
 * just no longer the loudest thing on screen.
 */
function Thought({ item }: { item: ThinkItem }) {
  const [open, setOpen] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  // Follow the text while it arrives. Unconditional, unlike the thread's own autoscroll: this box
  // is not something you read at your own pace, it is something you glance at.
  useEffect(() => {
    if (!item.streaming) return
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [item.text, item.streaming])

  if (item.streaming) {
    return (
      <div className="thought live">
        <div className="thought-head">
          <span className="spin" />
          <span>Thinking…</span>
        </div>
        <div className="thought-body" ref={bodyRef}>
          {item.text}
        </div>
      </div>
    )
  }

  return (
    <div className={`thought ${open ? 'open' : ''}`}>
      <button className="thought-head" onClick={() => setOpen((v) => !v)}>
        <span>{item.seconds ? `Thought for ${item.seconds}s` : 'Thought process'}</span>
        <span className="thought-caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="thought-body tall">{item.text}</div>}
    </div>
  )
}

function Item({ item }: { item: ThreadItem }) {
  switch (item.kind) {
    case 'scope':
      return (
        <div className="scope-row">
          <span className="scope-dot" />
          <span>
            Working on <b>{item.name}</b>
          </span>
          <span className="scope-path">agents/{item.id}/</span>
        </div>
      )

    case 'user':
      return (
        <div className="msg user">
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
          <span className="tile sm bot-mark">◈</span>
          {/* The renderer escapes every span of source before introducing a tag, so the only
              markup that reaches here is its own — see markdown/md.ts. */}
          <div
            className="bubble md"
            dangerouslySetInnerHTML={{
              __html:
                renderMarkdown(item.text) + (item.streaming ? '<span class="caret"></span>' : ''),
            }}
          />
        </div>
      )

    case 'think':
      return <Thought item={item} />

    case 'tool':
      return (
        <div className={`tool ${item.error ? 'err' : ''} ${item.done ? '' : 'run'}`}>
          <span className="mark">
            {item.done ? (item.error ? '✕' : '✓') : <span className="spin" />}
          </span>
          <span className="tname">{item.name}</span>
          <span className="targs">{item.args}</span>
        </div>
      )

    case 'fallback':
      return (
        <div className="tool err">
          <span className="mark">⚠</span>
          <span className="tname">{item.from} unavailable</span>
          <span className="targs">
            → {item.to} · {item.reason}
          </span>
        </div>
      )
  }
}

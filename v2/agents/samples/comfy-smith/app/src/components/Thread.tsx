/* The conversation, IN THE ORDER IT HAPPENED.
 *
 * A turn is an ordered list of blocks — reasoning, tool calls, prose — and this file renders
 * them in that order and nothing more. The temptation is to group: all the tools in a neat list,
 * then the text. Resist it. "It searched, then said this, then wrote the file" and "it said this,
 * then searched, then wrote the file" are different stories, and grouping tells neither.
 *
 * The tool rows are not decoration either. "It researched the model and wrote the file" and "it
 * wrote the file from memory" produce identical prose, and only one of them is trustworthy — so
 * the calls are on screen, in place, with their outcome.
 */

import { useEffect, useRef, useState } from 'react'
import type { Block, NoteBlock, ThinkingBlock, ThreadImage, ToolBlock, Turn } from '../agentd'

export interface Suggestion {
  title: string
  body: string
  cue: string
}

export function Thread({
  turns,
  suggestions,
  onPick,
}: {
  turns: Turn[]
  suggestions: Suggestion[]
  onPick: (text: string) => void
}) {
  const endRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  // AUTOSCROLL FOLLOWS THE READER. It stops the moment you scroll up to read something and
  // resumes when you come back to the bottom — an unconditional scrollIntoView yanks the page
  // out from under anyone trying to read while the agent is still talking.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (stickRef.current) endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns])

  if (!turns.length) {
    return (
      <div className="thread empty" ref={scrollRef}>
        <div className="hero">
          <span className="orb" aria-hidden />
          <h1>
            Your remote <em>ComfyUI engineer</em>
          </h1>
          <p>
            Point it at a ComfyUI anywhere — a pod on RunPod or Vast, a box on your network. It
            reads what that server has installed, builds against it, runs it, and fixes what
            fails.
          </p>
        </div>
        <div className="suggests">
          {suggestions.map((s) => (
            <button key={s.title} onClick={() => onPick(s.body)}>
              <strong>{s.title}</strong>
              <span>{s.body}</span>
              <span className="cue">{s.cue} →</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="thread" ref={scrollRef}>
      {turns.map((turn, i) => (
        <div key={i} className={`turn ${turn.role}`}>
          {/* Who is speaking, once per turn. Without it a transcript of plain paragraphs and
              tool rows reads as one long undifferentiated log. */}
          <span className="who">{turn.role === 'user' ? 'You' : 'Comfy Smith'}</span>
          <div className="turn-body">
            {turn.images.length > 0 && <Images images={turn.images} />}
            {turn.blocks.map((block, j) => (
              <BlockView key={j} block={block} live={turn.streaming} />
            ))}
            {turn.streaming && turn.blocks.length === 0 && (
              <div className="bubble waiting">
                <Dots />
              </div>
            )}
          </div>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  )
}

/** Three dots that actually move. A static "…" is indistinguishable from a stalled request. */
function Dots() {
  return (
    <span className="dots">
      <i />
      <i />
      <i />
    </span>
  )
}

function BlockView({ block, live }: { block: Block; live: boolean }) {
  if (block.kind === 'text') return <div className="bubble">{block.text}</div>
  if (block.kind === 'thinking') return <Thought block={block} live={live} />
  if (block.kind === 'note') return <Note block={block} />
  return <ToolRow block={block} />
}

/** The runtime's own words about why a run ended. Deliberately unlike a message: this is the
 *  system reporting, and reading it as something the agent "said" would be misleading. */
function Note({ block }: { block: NoteBlock }) {
  return (
    <div className={`note ${block.tone}`}>
      <span className="note-mark" aria-hidden>
        {block.tone === 'error' ? '!' : '↻'}
      </span>
      <span>{block.text}</span>
    </div>
  )
}

/** Reasoning: shown as it streams, folded away once the turn is done.
 *
 *  Both halves matter. Hiding it while it streams leaves the user staring at tool names with no
 *  idea what is being attempted; leaving it expanded afterwards buries the actual answer under
 *  the workings. `manual` records an explicit click so a fold the user opened stays open. */
function Thought({ block, live }: { block: ThinkingBlock; live: boolean }) {
  const [manual, setManual] = useState<boolean | null>(null)
  const open = manual ?? live
  return (
    <div className={`thought ${open ? 'open' : ''}`}>
      <button className="thought-head" onClick={() => setManual(!open)}>
        <span className="caret">{open ? '▾' : '▸'}</span>
        <span>{live ? 'Thinking…' : 'Thought process'}</span>
      </button>
      {open ? (
        <div className="thought-body">{block.text}</div>
      ) : (
        // One line of it when folded — enough to recognise the step you are looking for without
        // opening every fold in the transcript.
        <div className="thought-peek">{firstLine(block.text)}</div>
      )}
    </div>
  )
}

function ToolRow({ block }: { block: ToolBlock }) {
  const state = block.done ? (block.ok ? 'ok' : 'bad') : 'run'
  return (
    <div className={`tool ${state}`}>
      <span className="mark">{block.done ? (block.ok ? '✓' : '✕') : '·'}</span>
      <span className="tname">{block.name}</span>
      {/* While it runs, its own progress line; once it is done, the outcome. Showing progress
          after the fact would leave a stale "fetching page 3 of 8" next to a finished call. */}
      {!block.done && block.progress && <span className="tprogress">{block.progress}</span>}
      {block.done && block.detail && <span className="tdetail">{block.detail}</span>}
    </div>
  )
}

function Images({ images }: { images: ThreadImage[] }) {
  return (
    <div className="shots">
      {images.map((img, i) => (
        <a key={i} href={img.src} target="_blank" rel="noreferrer" title={img.name}>
          <img src={img.src} alt={img.name} />
        </a>
      ))}
    </div>
  )
}

function firstLine(text: string): string {
  const line = text.trim().split('\n').find((l) => l.trim()) || ''
  return line.length > 140 ? `${line.slice(0, 140)}…` : line
}

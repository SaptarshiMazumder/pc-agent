import { useEffect, useRef, useState } from 'react'
import type { Block, Turn } from '../agentd'

/** The secondary view: questions the list cannot answer.
 *
 *  IT RENDERS BLOCKS IN ORDER. Reasoning, answer text and tool calls interleave in a real run —
 *  the agent thinks, calls something, says what it found, calls something else. Walking one
 *  ordered array is what puts each thing where it happened; two parallel fields could only ever
 *  render every tool followed by every word.
 *
 *  THE COMPOSER IS NEVER DISABLED. The moment someone most needs to speak is mid-run — to correct
 *  a misread question, or to stop a long detour. So Ask becomes Stop and the input stays live. */
export function Ask({
  turns,
  busy,
  onSend,
  onStop,
}: {
  turns: Turn[]
  busy: boolean
  onSend: (text: string) => void
  onStop: () => void
}) {
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [turns])

  const submit = () => {
    if (!text.trim()) return
    onSend(text)
    setText('')
  }

  return (
    <div className="ask">
      <div className="messages">
        {turns.length === 0 && (
          <p className="muted">
            Ask across everything you have stored — "which of these disagree?", "what did I add
            this week?"
          </p>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn ${t.role}`}>
            {t.blocks.map((b, j) => (
              <BlockView key={j} block={b} />
            ))}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="composer">
        <input
          value={text}
          placeholder="Ask about your library"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
        />
        {busy ? (
          <button className="ghost" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button className="prime" onClick={submit} disabled={!text.trim()}>
            Ask
          </button>
        )}
      </div>
    </div>
  )
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case 'text':
      return <div className="bubble">{block.text}</div>
    case 'thinking':
      // Collapsed by default: it is context, not the answer. Present at all, so a long research
      // phase is visibly progress rather than a hang.
      return (
        <details className="thinking">
          <summary>thinking</summary>
          <div>{block.text}</div>
        </details>
      )
    case 'tool':
      return (
        <div className={`tool ${block.done ? (block.ok ? 'ok' : 'bad') : 'run'}`}>
          <span className="mark">{block.done ? (block.ok ? '✓' : '✕') : '·'}</span>
          <span className="tool-name">{block.name}</span>
          {/* Progress while it runs, the summary once it finishes — never both, never neither. */}
          {!block.done && block.progress && <span className="tool-note">{block.progress}</span>}
          {block.done && block.detail && <span className="tool-note">{block.detail}</span>}
        </div>
      )
    case 'note':
      return <div className={`note-block ${block.tone}`}>{block.text}</div>
  }
}

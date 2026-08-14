import { useEffect, useRef, useState } from 'react'
import type { Turn } from '../agentd'

/** The secondary view: questions the list cannot answer.
 *
 *  Kept deliberately small. This agent's job is the library; a full chat client here would
 *  invite using it as one, and every answer given in chat is a note that did not get written. */
export function Ask({
  turns,
  busy,
  onSend,
}: {
  turns: Turn[]
  busy: boolean
  onSend: (text: string) => void
}) {
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [turns])

  const submit = () => {
    if (busy || !text.trim()) return
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
            {t.tools.length > 0 && (
              <ul className="tools">
                {t.tools.map((r) => (
                  <li key={r.id} className={r.done ? (r.ok ? 'ok' : 'bad') : 'run'}>
                    <span className="mark">{r.done ? (r.ok ? '✓' : '✕') : '·'}</span>
                    {r.name}
                  </li>
                ))}
              </ul>
            )}
            {t.text && <div className="bubble">{t.text}</div>}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="composer">
        <input
          value={text}
          placeholder={busy ? 'thinking…' : 'Ask about your library'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
        />
        <button className="prime" onClick={submit} disabled={busy || !text.trim()}>
          Ask
        </button>
      </div>
    </div>
  )
}

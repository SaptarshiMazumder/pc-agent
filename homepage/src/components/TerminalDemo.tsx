import { useEffect, useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import { usePrefersReducedMotion } from '../lib/usePrefersReducedMotion'

type Line =
  | { kind: 'thought'; text: string }
  | { kind: 'tool'; name: string; arg: string; meta: string }
  | { kind: 'answer'; text: string }

const PROMPT =
  'Read movies_watchlist.txt off my desktop, find where each title streams in Japan, and write me the list with links.'

const LINES: Line[] = [
  { kind: 'thought', text: 'Reading the file first, then resolving each title.' },
  { kind: 'tool', name: 'read', arg: '~/Desktop/movies_watchlist.txt', meta: '12 titles' },
  { kind: 'tool', name: 'web_search', arg: '"Perfect Blue" streaming Japan', meta: '8 results' },
  { kind: 'tool', name: 'browser', arg: 'justwatch.com/jp', meta: 'resolved 12 / 12' },
  { kind: 'tool', name: 'write', arg: '~/Desktop/watchlist_links.md', meta: '3.1 KB' },
  {
    kind: 'answer',
    text: 'All 12 found — 9 on Netflix JP, 2 on U-NEXT, 1 rental only. Written to watchlist_links.md on your desktop.',
  },
]

const TYPE_MS = 22
const LINE_MS = 620

export function TerminalDemo() {
  const reducedMotion = usePrefersReducedMotion()
  const [typed, setTyped] = useState(reducedMotion ? PROMPT.length : 0)
  const [shown, setShown] = useState(reducedMotion ? LINES.length : 0)

  // Type the prompt one character at a time.
  useEffect(() => {
    if (reducedMotion || typed >= PROMPT.length) return
    const timer = window.setTimeout(() => setTyped((n) => n + 1), TYPE_MS)
    return () => window.clearTimeout(timer)
  }, [typed, reducedMotion])

  // Then reveal the transcript, one line at a time.
  useEffect(() => {
    if (reducedMotion) return
    if (typed < PROMPT.length || shown >= LINES.length) return
    const delay = shown === 0 ? 520 : LINE_MS
    const timer = window.setTimeout(() => setShown((n) => n + 1), delay)
    return () => window.clearTimeout(timer)
  }, [typed, shown, reducedMotion])

  const typingDone = typed >= PROMPT.length
  const running = shown < LINES.length

  return (
    <div className="term" role="img" aria-label="A transcript of agentd reading a local file, searching the web, and writing the result back to disk.">
      <div className="term__chrome">
        <span className="term__dot" />
        <span className="term__dot" />
        <span className="term__dot" />
        <span className="term__title">agentd — main</span>
        <span className={`term__status ${running ? 'is-running' : ''}`}>
          {running ? 'working' : 'done'}
        </span>
      </div>

      <div className="term__body">
        <p className="term__prompt">
          <span className="term__caret-mark">&gt;</span>
          <span>
            {PROMPT.slice(0, typed)}
            {!typingDone && <span className="term__caret" />}
          </span>
        </p>

        <div className="term__stream" aria-live="off">
          {LINES.slice(0, shown).map((line, index) => {
            const isLast = index === shown - 1
            if (line.kind === 'thought') {
              return (
                <p key={index} className="term__thought">
                  {line.text}
                </p>
              )
            }
            if (line.kind === 'tool') {
              const done = !isLast || !running
              return (
                <p key={index} className="term__tool">
                  <span className={`term__tool-icon ${done ? 'is-done' : ''}`}>
                    {done ? <Check size={13} /> : <Loader2 size={13} className="spin" />}
                  </span>
                  <span className="term__tool-name">{line.name}</span>
                  <span className="term__tool-arg">{line.arg}</span>
                  <span className="term__tool-meta">{line.meta}</span>
                </p>
              )
            }
            return (
              <p key={index} className="term__answer">
                {line.text}
              </p>
            )
          })}
        </div>
      </div>
    </div>
  )
}

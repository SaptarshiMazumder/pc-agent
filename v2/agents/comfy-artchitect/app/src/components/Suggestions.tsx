/* What to do next, as buttons — the difference between a chat and a tool you can drive.
 *
 * THE PROBLEM IT SOLVES. An agent that ends a turn with "let me know how you'd like to proceed"
 * hands the user a blank text box and a vocabulary problem: they have to guess what this agent can
 * be asked for, and type it. Every good agent product answers that for them — the next moves are
 * offered, phrased as the user's own words, one click away.
 *
 * HOW THE AGENT EMITS THEM. A fenced block at the end of its message:
 *
 *     ```suggest
 *     Test it now | Run the workflow and show me the result
 *     Make it faster | Cut the steps down without changing the look
 *     ```
 *
 * `label | prompt` per line — the label is the button, the prompt is what gets SENT. The parser
 * strips the block from the rendered prose, so a window that has not been updated simply shows a
 * small code block instead of breaking. No new tool, no protocol change, and any agent can do it.
 *
 * THESE ARE NOT A GATE. The agent has already done the work and moved on; a chip redirects it, it
 * does not unblock it. That distinction is the whole reason this is safe to add to an agent we
 * spent real effort teaching not to stop and ask.
 */

import { ArrowUpRight } from 'lucide-react'

export interface Suggestion {
  label: string
  prompt: string
}

/** The fence, and everything in it. Tolerant of ``` and ~~~ and of a missing closer (a stream cut
 *  short mid-block should not leave the raw fence in the transcript). */
const BLOCK = /(?:```|~~~)\s*suggest\s*\n([\s\S]*?)(?:```|~~~|$)/i

/** Split a bot message into the prose to render and the chips to offer. */
export function parseSuggestions(text: string): { body: string; suggestions: Suggestion[] } {
  const m = BLOCK.exec(text || '')
  if (!m) return { body: text, suggestions: [] }
  const suggestions: Suggestion[] = []
  for (const line of m[1].split('\n')) {
    const raw = line.trim().replace(/^[-*]\s*/, '')
    if (!raw) continue
    const [label, ...rest] = raw.split('|')
    const prompt = rest.join('|').trim()
    const shown = label.trim()
    if (!shown) continue
    // A line with no `|` is still usable: the label doubles as the prompt.
    suggestions.push({ label: shown, prompt: prompt || shown })
    if (suggestions.length >= 4) break // four is a menu; ten is another wall of text
  }
  return { body: (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim(), suggestions }
}

export function Suggestions({
  items,
  onPick,
  disabled,
}: {
  items: Suggestion[]
  onPick: (prompt: string) => void
  /** While a run is going, clicking would queue a second one — offer them, but inert. */
  disabled?: boolean
}) {
  if (!items.length) return null
  return (
    <div className="suggests">
      {items.map((s) => (
        <button
          key={s.label}
          className="suggest-chip"
          disabled={disabled}
          onClick={() => onPick(s.prompt)}
          title={s.prompt}
        >
          <span>{s.label}</span>
          <ArrowUpRight size={13} strokeWidth={1.9} />
        </button>
      ))}
    </div>
  )
}

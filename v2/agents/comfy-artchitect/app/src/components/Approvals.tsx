/* Which paid services the agent may use — asked once, before it builds anything.
 *
 * THE PROBLEM IT SOLVES. The agent now ranks models on fitness and will pick a hosted service
 * when that is genuinely the best answer. Good — but the bill lands on the user, and "I'll use
 * Seedance" buried in a paragraph is not consent. Worse, the cost is discovered at RUN time,
 * after the workflow is designed around that node, when saying no means throwing the design away.
 *
 * So the question is asked at the only moment it is cheap to answer: the plan is settled, nothing
 * is built yet, and switching costs a redesign the agent has not done rather than one it has.
 *
 * HOW THE AGENT EMITS IT. A fenced block at the end of its message:
 *
 *     ```approve
 *     seedance | Seedance 2.0 (ByteDance) | the final 8s video — paid per run
 *     veo      | Veo 3.1 (Google)         | storyboard frames — paid per image
 *     ```
 *
 * `id | service | what it is for`. Same mechanism as `suggest`: no new tool, no protocol change,
 * and a window that has not been updated shows a small code block rather than breaking.
 *
 * NOTHING IS TICKED BY DEFAULT. A pre-ticked box is not consent, it is a dark pattern with a
 * checkbox on it — and the whole point of asking is that the user's money is involved.
 *
 * THIS ONE IS A GATE, and that is the difference from `suggest`. Chips redirect an agent that has
 * already moved on; this stops it until it is answered. That is the one case the "never stop and
 * ask" rule yields to, because spending someone's money without asking is worse than a pause.
 */

import { Check } from 'lucide-react'
import { useState } from 'react'

export interface Approval {
  id: string
  service: string
  usedFor: string
}

/** The fence, and everything in it. Tolerant of ``` and ~~~ and of a missing closer, so a stream
 *  cut short mid-block does not leave a raw fence in the transcript. */
const BLOCK = /(?:```|~~~)\s*approve\s*\n([\s\S]*?)(?:```|~~~|$)/i

/** Split a bot message into the prose to render and the paid services to confirm. */
export function parseApprovals(text: string): { body: string; approvals: Approval[] } {
  const m = BLOCK.exec(text || '')
  if (!m) return { body: text, approvals: [] }
  const approvals: Approval[] = []
  for (const line of m[1].split('\n')) {
    const raw = line.trim().replace(/^[-*]\s*/, '')
    if (!raw) continue
    const [id, service, ...rest] = raw.split('|')
    const key = id.trim()
    if (!key) continue
    approvals.push({
      id: key,
      // A line with only an id is still usable — better a bare name than a dropped question.
      service: (service || '').trim() || key,
      usedFor: rest.join('|').trim(),
    })
    if (approvals.length >= 8) break
  }
  return { body: (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim(), approvals }
}

export function Approvals({
  items,
  onDecide,
}: {
  items: Approval[]
  onDecide: (reply: string) => void
}) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set())
  const [sent, setSent] = useState(false)
  if (!items.length) return null

  const toggle = (id: string): void =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (!next.delete(id)) next.add(id)
      return next
    })

  const confirm = (): void => {
    const yes = items.filter((i) => picked.has(i.id))
    const no = items.filter((i) => !picked.has(i.id))
    // NAMED BOTH WAYS, never "approved: none". The agent has to act differently on a decline, and
    // an empty list reads as an unanswered question rather than as a decision that was made.
    const reply =
      (yes.length ? `Approved: ${yes.map((i) => i.service).join(', ')}. ` : 'Approved: none. ') +
      (no.length ? `Declined: ${no.map((i) => i.service).join(', ')}.` : '')
    setSent(true)
    onDecide(reply.trim())
  }

  return (
    <div className="approve" role="group" aria-label="Paid services to approve">
      <p className="approve-head">
        These cost money to run. Tick the ones you are happy to pay for — the agent builds around
        your answer.
      </p>
      {items.map((a) => (
        <label key={a.id} className={`approve-row${picked.has(a.id) ? ' is-on' : ''}`}>
          <input
            type="checkbox"
            checked={picked.has(a.id)}
            onChange={() => toggle(a.id)}
            disabled={sent}
          />
          <span className="approve-text">
            <span className="approve-name">{a.service}</span>
            {a.usedFor && <span className="approve-for">{a.usedFor}</span>}
          </span>
        </label>
      ))}
      <button className="approve-go" onClick={confirm} disabled={sent}>
        <Check size={14} strokeWidth={2.2} />
        {sent ? 'Sent' : picked.size ? `Use ${picked.size} of ${items.length}` : 'Use none of these'}
      </button>
    </div>
  )
}

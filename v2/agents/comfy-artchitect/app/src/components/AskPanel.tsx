/* The ask — `ask_user` rendered as the thing it is: a checkbox per paid service, an answer box per
 * brief-check question, the workflows that will be built, one button.
 *
 * FROM THE CALL'S ARGUMENTS, NOT FROM PROSE. This used to be a fenced ```approve block parsed out
 * of the assistant's text, and every model spelled it a little differently — a ```text fence, a
 * missing pipe, the block mid-message — each of which rendered as a code block instead of
 * checkboxes and, worse, never armed the daemon's paid gate. A tool call has a schema: the daemon
 * validated the arguments before this panel sees them (plugins/ask), and the panel reads them the
 * way PlanBlock reads update_plan's plan — live, with no round-trip, and after a reload from the
 * transcript, where the call's arguments still are.
 *
 * NOTHING IS TICKED BY DEFAULT. A pre-ticked box is not consent, it is a dark pattern with a
 * checkbox on it — and the whole point of asking is that the user's money is involved. The
 * questions, by contrast, ARE prefilled: the agent's default is the answer it will use if the
 * user says nothing, so showing it is showing the truth.
 *
 * THE REPLY IS A MESSAGE. Pressing the button SENDS — ticking boxes and pressing was already the
 * deliberate act; making the user press Enter after it would be asking twice. The daemon stamps
 * that message as the answer to the ask (checkpoint_marker.answer), which is what lets
 * comfy_install / comfy_run proceed. It is worded for the model: the services approved and
 * declined by name, and every answer beside its question.
 *
 * ANSWERED IS A FACT OF THE THREAD, not of this component: a user message after the call means the
 * question was answered — by this button or by typing — and the panel shows as such after a reload
 * too. `sent` covers the moment between the click and that message landing.
 */

import { Check } from 'lucide-react'
import { useState } from 'react'

import type { ThreadItem } from '../agentd/chat'

type ToolItem = Extract<ThreadItem, { kind: 'tool' }>

interface Service {
  name: string
  purpose: string
  credits: number
}
interface Question {
  question: string
  default: string
}
interface Workflow {
  name: string
  does: string
}
interface Reference {
  role: string
  what: string
}

const text = (v: unknown): string => (typeof v === 'string' || typeof v === 'number' ? String(v).trim() : '')
const num = (v: unknown): number => {
  if (typeof v === 'number') return v
  if (typeof v === 'string') return Number(v.replace(/[$,\s]/g, '')) || 0
  return 0
}

/** The call's rows of one kind, tolerantly: the daemon already refused anything unusable, so a
 *  row missing here is one the model never sent. */
function rows<T>(v: unknown, pick: (r: Record<string, unknown>) => T | null): T[] {
  if (!Array.isArray(v)) return []
  const out: T[] = []
  for (const r of v) {
    if (!r || typeof r !== 'object') continue
    const row = pick(r as Record<string, unknown>)
    if (row) out.push(row)
  }
  return out
}

/* CREDITS ONLY. The dollar figure is the platform's own provider cost and meant nothing to the
   person reading it — they hold a credit balance, they spend credits, and two numbers for one
   price invited the question "so which am I being charged?". One unit, the one they actually
   have. */
const price = (s: Service): string =>
  s.credits > 0 ? `${s.credits.toLocaleString()} credits` : 'free — runs on the rented GPU'

export function AskPanel({
  item,
  answered,
  onDecide,
}: {
  item: ToolItem
  /** A user message follows this call in the thread — the ask has its answer, however given. */
  answered: boolean
  onDecide?: (reply: string) => void
}) {
  const args = item.args as Record<string, unknown>
  const title = text(args.title)
  const services = rows<Service>(args.services, (r) =>
    text(r.name) ? { name: text(r.name), purpose: text(r.purpose), credits: num(r.credits) } : null,
  )
  const questions = rows<Question>(args.questions, (r) =>
    text(r.question) ? { question: text(r.question), default: text(r.default) } : null,
  )
  const workflows = rows<Workflow>(args.workflows, (r) =>
    text(r.name) ? { name: text(r.name), does: text(r.does) } : null,
  )
  const references = rows<Reference>(args.references, (r) =>
    text(r.role) ? { role: text(r.role).replace(/^@/, ''), what: text(r.what) } : null,
  )

  const [picked, setPicked] = useState<Set<number>>(() => new Set())
  const [answers, setAnswers] = useState<Record<number, string>>({})
  const [sent, setSent] = useState(false)
  /* THE OTHER BOX — always there, like the "Other" on any real question. The listed services
     are the agent's picks, not the only options: the person may want a different model or
     provider, something cheaper, or free/open-source only, and none of that fits a checkbox.
     What they type goes to the agent verbatim as "Instead: …", which the protocol treats as
     design input (research it, price it, ask once more). */
  const [other, setOther] = useState('')
  const closed = sent || answered || !onDecide

  const toggle = (i: number): void =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (!next.delete(i)) next.add(i)
      return next
    })

  const touched = questions.some((q, i) => (answers[i] ?? q.default) !== q.default)

  const confirm = (): void => {
    const lines: string[] = []
    const instead = other.trim()
    const declined = services.filter((_, i) => !picked.has(i))
    if (services.length) {
      const yes = services.filter((_, i) => picked.has(i))
      // NAMED BOTH WAYS, never "approved: none" alone. The agent has to act differently on a
      // decline, and an empty list reads as an unanswered question rather than as a decision.
      lines.push(
        (yes.length ? `Approved: ${yes.map((s) => s.name).join(', ')}.` : 'Approved: none.') +
          (declined.length ? ` Declined: ${declined.map((s) => s.name).join(', ')}.` : ''),
      )
      // A DECLINE IS ABOUT THAT SERVICE. Said in the answer itself, so no model reads "no to
      // Seedance" as "no to anything paid": the only thing that switches a job to free is the
      // person saying so — which is exactly what the Other box is for.
      if (declined.length && !instead) {
        lines.push('Declined means those services only; any other option, paid or free, is fine.')
      }
    } else {
      lines.push('Build it as proposed.')
    }
    if (instead) lines.push(`Instead: ${instead}`)
    if (questions.length) {
      lines.push('Answers:')
      questions.forEach((q, i) => lines.push(`- ${q.question} — ${(answers[i] ?? q.default).trim() || q.default}`))
    }
    setSent(true)
    onDecide?.(lines.join('\n'))
  }

  const label = answered
    ? 'Answered'
    : sent
      ? 'Sent'
      : other.trim()
        ? 'Send my answer'
        : services.length
        ? picked.size
          ? `Use ${picked.size} of ${services.length}${questions.length ? ' and answer' : ''}`
          : 'Use none of these'
        : touched
          ? 'Build with these answers'
          : 'Keep the defaults and build'

  return (
    <div className="approve ask" role="group" aria-label="Before anything is built">
      <p className="ask-title">{title || 'Before anything is built:'}</p>
      {services.length > 0 && (
        <>
          <p className="approve-head">
            These models use more credits. Tick the ones you want — the agent builds around your
            answer.
          </p>
          {services.map((s, i) => (
            <label key={i} className={`approve-row${picked.has(i) ? ' is-on' : ''}`}>
              <input type="checkbox" checked={picked.has(i)} onChange={() => toggle(i)} disabled={closed} />
              <span className="approve-text">
                <span className="approve-name">{s.name}</span>
                <span className="approve-for">
                  {s.purpose}
                  {s.purpose ? ' — ' : ''}
                  {price(s)}
                </span>
              </span>
            </label>
          ))}
        </>
      )}
      {questions.length > 0 && (
        <>
          <p className="ask-section">The brief — change anything, or keep the defaults</p>
          {questions.map((q, i) => (
            <label key={i} className="ask-q">
              <span className="ask-q-label">{q.question}</span>
              <input
                type="text"
                value={answers[i] ?? q.default}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [i]: e.target.value }))}
                disabled={closed}
              />
            </label>
          ))}
        </>
      )}
      {workflows.length > 0 && (
        <>
          <p className="ask-section">What gets built, in order</p>
          <ol className="ask-wf">
            {workflows.map((w, i) => (
              <li key={i}>
                <b>{w.name}</b>
                {w.does ? ` — ${w.does}` : ''}
              </li>
            ))}
          </ol>
        </>
      )}
      {references.length > 0 && (
        <>
          <p className="ask-section">Files it needs — add them in the References panel on the left</p>
          <ol className="ask-wf">
            {references.map((r, i) => (
              <li key={i}>
                <b>@{r.role}</b>
                {r.what ? ` — ${r.what}` : ''}
              </li>
            ))}
          </ol>
        </>
      )}
      <label className="ask-q ask-other">
        <span className="ask-q-label">Something else?</span>
        <textarea
          rows={2}
          placeholder="Want a different model or provider, something cheaper, or free / open-source models only? Say so here."
          value={other}
          onChange={(e) => setOther(e.target.value)}
          disabled={closed}
        />
      </label>
      <button className="approve-go" onClick={confirm} disabled={closed}>
        <Check size={14} strokeWidth={2.2} />
        {label}
      </button>
    </div>
  )
}

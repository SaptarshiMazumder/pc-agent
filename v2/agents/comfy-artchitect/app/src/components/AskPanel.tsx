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
 * A MODEL IS AN OPTION FOR A STEP. Options carrying the same `step` (a workflow's role) are
 * alternatives for it: grouped under that step, one pick each, and the answer cannot be sent until
 * every step has one. A flat list of checkboxes read as "tick whichever you like" — a person ticked
 * the try-on alone, the campaign step went unanswered, and the agent asked about four replacement
 * models in a row. Options without a step stay plain checkboxes, as before.
 *
 * ANSWERED IS A FACT OF THE THREAD, not of this component: a user message after the call means the
 * question was answered — by this button or by typing — and the panel shows as such after a reload
 * too. `sent` covers the moment between the click and that message landing.
 */

import { Check, ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'

import type { ThreadItem } from '../agentd/chat'
import { askOf } from '../agentd/chat'
import { lengthText, picturesText, type StageFacts } from '../agentd/stage-plan'

type ToolItem = Extract<ThreadItem, { kind: 'tool' }>

interface Service {
  name: string
  purpose: string
  credits: number
  /** The workflow step this option is for; "" = an optional extra with no step. */
  step: string
}
interface Question {
  question: string
  default: string
}
interface Workflow {
  name: string
  does: string
  /** pipeline_present's steps carry what the stage will do, read off its graph. */
  facts: StageFacts | null
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
  s.credits > 0 ? `${s.credits.toLocaleString()} credits` : 'no credits — runs on your Comfy plan'

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
  const args = askOf(item) || {}
  const title = text(args.title)
  const services = rows<Service>(args.services, (r) =>
    text(r.name)
      ? { name: text(r.name), purpose: text(r.purpose), credits: num(r.credits), step: text(r.step) }
      : null,
  )
  const questions = rows<Question>(args.questions, (r) =>
    text(r.question) ? { question: text(r.question), default: text(r.default) } : null,
  )
  const workflows = rows<Workflow>(args.workflows, (r) =>
    text(r.name)
      ? {
          name: text(r.name),
          does: text(r.does),
          facts: r.facts && typeof r.facts === 'object' ? (r.facts as StageFacts) : null,
        }
      : null,
  )
  /* A PIPELINE CARD (pipeline_present): its steps carry facts, so the card is drawn as short lines
     of what will run — inputs, size, length, model — with no paragraph above them. */
  const factual = workflows.some((w) => w.facts)
  const delivers = text(args.delivers)
  const results = text(args.results)
  const imports = text(args.imports)
  const spend = text(args.spend)
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
  /** An answered card is folded to one line; this opens it again. */
  const [unfolded, setUnfolded] = useState(false)
  const closed = sent || answered || !onDecide

  /* THE STEPS, in the order they are built: the workflows' order first, then any step the
     workflows do not name, in the order its options came. */
  const stepNames = [
    ...workflows.map((w) => w.name).filter((n) => services.some((s) => s.step === n)),
    ...services.map((s) => s.step).filter((n, i, all) => n && all.indexOf(n) === i),
  ].filter((n, i, all) => all.indexOf(n) === i)
  const stepLabel = (name: string): string => workflows.find((w) => w.name === name)?.does || name
  const missing = stepNames.filter((n) => !services.some((s, i) => s.step === n && picked.has(i)))

  // ONE PICK PER STEP: ticking an option clears the step's other options; a plain option toggles.
  const toggle = (i: number): void =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.delete(i)) return next
      const step = services[i].step
      if (step) services.forEach((s, j) => s.step === step && next.delete(j))
      next.add(i)
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
      // THE PICK FOR EVERY STEP, said outright: each step is answered, so there is nothing left
      // to find a replacement for.
      if (stepNames.length) {
        lines.push(
          'Chosen for each step: ' +
            stepNames
              .map((n) => `${n} → ${services.filter((s, i) => s.step === n && picked.has(i)).map((s) => s.name).join(', ') || '(none)'}`)
              .join('; ') +
            '.',
        )
      }
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

  /* CLAUDE'S QUESTION STYLE (Oct 2026). Every choice is a ROW — a round marker (a square one when
     several may be ticked), its name in bold, one dim line under it; the picked row is lit. Each
     decision is its own block with a bold question. Once answered the card FOLDS to one line,
     "Answered · N questions", and opens again on a click — the thread stays readable, and what
     was decided is one click away. Nothing about what is asked or sent changed; only the look. */
  const row = (s: Service, i: number) => {
    const on = picked.has(i)
    return (
      <label key={i} className={`cq-opt${on ? ' is-on' : ''}`}>
        <input
          className="cq-input"
          type={s.step ? 'radio' : 'checkbox'}
          name={s.step ? `step-${item.id}-${s.step}` : undefined}
          checked={on}
          onChange={() => toggle(i)}
          onClick={() => s.step && picked.has(i) && toggle(i)}
          disabled={closed}
        />
        <span className={`cq-mark${s.step ? '' : ' is-box'}`} aria-hidden="true" />
        <span className="cq-text">
          <span className="cq-label">
            {s.name}
            <span className={`cq-price${s.credits > 0 ? '' : ' is-free'}`}>{price(s)}</span>
          </span>
          {s.purpose && <span className="cq-desc">{s.purpose}</span>}
        </span>
      </label>
    )
  }

  /* A STEP WITHOUT A PICK IS AN UNANSWERED QUESTION — the answer is not sent with one open,
     unless the person wrote something else instead (the Other box is always an answer). */
  const blocked = missing.length > 0 && !other.trim()

  /* THE RUNNING TOTAL of what is ticked — display only, the same credits each row already
     shows, added up so the decision reads as one number. */
  const tickedCredits = services.reduce((n, s, i) => (picked.has(i) ? n + s.credits : n), 0)

  const head = factual
    ? [title, `${workflows.length} step${workflows.length === 1 ? '' : 's'}`, delivers && `delivers ${delivers}`, results]
        .filter(Boolean)
        .join(' · ')
    : title || 'Before anything is built:'

  const extras = services.some((s) => !s.step)
  const asked = Math.max(1, stepNames.length + (extras ? 1 : 0) + questions.length)
  const words = (s: string): string => s.replace(/_/g, ' ')

  if (closed && !unfolded) {
    return (
      <button type="button" className="cq-folded" onClick={() => setUnfolded(true)} aria-expanded={false}>
        {sent && !answered ? 'Sent' : 'Answered'} · {asked} question{asked === 1 ? '' : 's'}
        <ChevronDown size={14} />
      </button>
    )
  }

  return (
    <div className="cq" role="group" aria-label="Before anything is built">
      {closed && (
        <button type="button" className="cq-folded is-open" onClick={() => setUnfolded(false)} aria-expanded>
          {sent && !answered ? 'Sent' : 'Answered'} · {asked} question{asked === 1 ? '' : 's'}
          <ChevronUp size={14} />
        </button>
      )}
      <div className="cq-body">
        <p className="cq-head">{head}</p>

        {stepNames.map((n) => (
          <div key={n} className="cq-q">
            <p className="cq-title">
              {stepLabel(n)} — which model?
              {!closed && missing.includes(n) && <span className="cq-req">pick one</span>}
            </p>
            {services.map((s, i) => (s.step === n ? row(s, i) : null))}
          </div>
        ))}
        {extras && (
          <div className="cq-q">
            <p className="cq-title">{stepNames.length ? 'Optional extras' : 'Which of these should it use?'}</p>
            {services.map((s, i) => (s.step ? null : row(s, i)))}
          </div>
        )}

        {factual &&
          workflows.map((w, i) => (
            <div key={i} className="cq-q">
              <p className="cq-title">
                {i + 1}. {words(w.name)}
                {w.facts && <span className="cq-chip">{w.facts.model}</span>}
              </p>
              {w.facts && <StepFacts facts={w.facts} />}
            </div>
          ))}
        {!factual && workflows.length > 0 && (
          <div className="cq-q">
            <p className="cq-title">What gets built, in order</p>
            <ol className="cq-list">
              {workflows.map((w, i) => (
                <li key={i}>
                  <b>{w.name}</b>
                  {w.does ? ` — ${w.does}` : ''}
                </li>
              ))}
            </ol>
          </div>
        )}

        {questions.map((q, i) => {
          const value = answers[i] ?? q.default
          const edit = (v: string) => setAnswers((prev) => ({ ...prev, [i]: v }))
          const long = q.default.length > 80 || q.default.includes('\n')
          return (
            <label key={i} className="cq-q">
              <span className="cq-title">{q.question}</span>
              {/* A PROMPT IS A PARAGRAPH: a one-line box showed forty words of a thousand. */}
              {long ? (
                <textarea className="cq-field" rows={3} value={value} onChange={(e) => edit(e.target.value)} disabled={closed} />
              ) : (
                <input className="cq-field" type="text" value={value} onChange={(e) => edit(e.target.value)} disabled={closed} />
              )}
            </label>
          )
        })}

        {references.length > 0 &&
          (factual ? (
            <p className="cq-note">Add your files in the Workspace: {references.map((r) => words(r.role)).join(', ')}</p>
          ) : (
            <div className="cq-q">
              <p className="cq-title">Photos it needs — add them under Inputs, below this card</p>
              <ol className="cq-list">
                {references.map((r, i) => (
                  <li key={i}>
                    <b>{r.role}</b>
                    {r.what ? ` — ${r.what}` : ''}
                  </li>
                ))}
              </ol>
            </div>
          ))}

        <label className="cq-q">
          <span className="cq-title">Changes?</span>
          <textarea
            className="cq-field"
            rows={2}
            placeholder="A different model, size, length, something cheaper, free models only…"
            value={other}
            onChange={(e) => setOther(e.target.value)}
            disabled={closed}
          />
        </label>
      </div>

      <div className="cq-foot">
        <span className="cq-total">
          {[spend, !services.length ? imports : '', services.length ? (picked.size ? `Ticked ${tickedCredits.toLocaleString()} credits` : 'Nothing ticked') : '']
            .filter(Boolean)
            .join(' · ')}
        </span>
        {!closed && blocked && <span className="ask-missing">Pick one for: {missing.map(stepLabel).join(', ')}</span>}
        {!closed && (
          <button className="approve-go" onClick={confirm} disabled={blocked}>
            <Check size={14} strokeWidth={2.2} />
            {label}
          </button>
        )}
      </div>
    </div>
  )
}

/** One step's facts as short lines: what it reads, its size and length, its LoRAs. */
function StepFacts({ facts }: { facts: StageFacts }) {
  const words = (s: string): string => s.replace(/_/g, ' ')
  const inputs = facts.inputs.map(
    (i) =>
      `${words(i.role)} (${i.from === 'you' ? 'yours' : `from ${words(i.from)}`}` +
      `${i.frame ? `, ${i.frame === 'first' ? 'opens on it' : 'ends on it'}` : ''})`,
  )
  const shape = [picturesText(facts), facts.size, lengthText(facts)].filter(Boolean).join(' · ')
  return (
    <ul className="ask-facts">
      {inputs.length > 0 && <li>Inputs: {inputs.join(', ')}</li>}
      {shape && <li>{shape}</li>}
      {facts.loras.length > 0 && <li>LoRA: {facts.loras.join(', ')}</li>}
      {facts.review && <li>You see it before the next step</li>}
    </ul>
  )
}

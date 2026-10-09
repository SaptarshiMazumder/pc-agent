/* The Design phase on the studio: the agent's card — the SAME AskPanel the thread renders, with
 * the same answer handler — and the inputs the design declared, right where they are needed.
 *
 * Before there is a card: what is happening (the agent designing, or nothing said yet). After
 * it is answered the studio moves on to the stages; the answered card stays in the thread as
 * history. */

import { Loader2 } from 'lucide-react'

import type { Artifact } from '../../agentd/artifacts'
import type { ToolItem } from '../../agentd/creation-phase'
import type { Slot } from '../../agentd/reference-slots'
import { AskPanel } from '../AskPanel'
import { ReferenceSlots } from './ReferenceSlots'

export function DesignPhase({
  ask,
  onDecide,
  hasPlan,
  running,
  slots,
  free,
  referencesDisabled,
  onAddReference,
  onOpen,
  onFromLibrary,
}: {
  /** The card waiting for an answer, or null. */
  ask: ToolItem | null
  onDecide: (reply: string) => void
  hasPlan: boolean
  running: boolean
  slots: Slot[]
  free: Artifact[]
  referencesDisabled: boolean
  onAddReference: (file: File, role: string | null) => Promise<void>
  onOpen: (a: Artifact) => void
  onFromLibrary: (role: string) => void
}) {
  return (
    <div className="dp">
      {ask ? (
        <section className="dp-card">
          <div className="dp-card-head">
            <span className="dp-eyebrow">The design</span>
            <h3 className="dp-title">Approve it, then it sets up and runs</h3>
            <p className="dp-sub">
              Each step's model, inputs, size and prompt, read off the design. Edit a prompt in place; &ldquo;Instead:&rdquo; redesigns it.
            </p>
          </div>
          <AskPanel item={ask} answered={false} onDecide={onDecide} />
        </section>
      ) : (
        <section className="dp-wait">
          {running ? (
            <>
              <Loader2 className="ld-spin" size={18} strokeWidth={1.8} />
              <span>{hasPlan ? 'Penguin is reworking the design…' : 'Penguin is picking the models and writing the stages…'}</span>
            </>
          ) : (
            <span>Describe what you want in the chat — the design card appears here for you to approve.</span>
          )}
        </section>
      )}

      <section className="dp-inputs">
        <span className="stg-label">Inputs the design reads</span>
        <ReferenceSlots slots={slots} free={free} disabled={referencesDisabled} onAdd={onAddReference} onOpen={onOpen} onFromLibrary={onFromLibrary} />
      </section>
    </div>
  )
}

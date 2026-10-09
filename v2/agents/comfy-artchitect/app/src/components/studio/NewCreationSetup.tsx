/* The studio side of a new creation — nothing said yet. Three ways in (describe it in the chat, a
 * template, a workflow you kept), the inputs the job will read (reachable BEFORE the first message
 * — they used to appear only once the agent had declared a slot), and the kept workflows to run
 * again. The chat beside it is where a description starts. */

import { ArrowRight, LayoutTemplate, MessageSquareText, Workflow } from 'lucide-react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact } from '../../agentd/artifacts'
import type { LibraryItem } from '../../agentd/library'
import type { Slot } from '../../agentd/reference-slots'
import type { LibraryTab } from '../../state/store'
import { StarterWorkflows } from '../StarterWorkflows'
import { ReferenceSlots } from './ReferenceSlots'

export function NewCreationSetup({
  client,
  workspaceVersion,
  slots,
  free,
  referencesDisabled,
  onAddReference,
  onOpen,
  onFromLibrary,
  onOpenLibrary,
  onRunAgain,
}: {
  client: AgentdClient | undefined
  workspaceVersion: number
  slots: Slot[]
  free: Artifact[]
  referencesDisabled: boolean
  onAddReference: (file: File, role: string | null) => Promise<void>
  onOpen: (a: Artifact) => void
  onFromLibrary: (role: string) => void
  onOpenLibrary: (tab: LibraryTab) => void
  onRunAgain: (item: LibraryItem) => void
}) {
  return (
    <div className="nc">
      <header className="nc-head">
        <span className="ch-eyebrow">New creation</span>
        <h2 className="nc-title">What are we making?</h2>
        <p className="nc-blurb">
          Describe it in the chat and Penguin designs it — picks the models, writes every stage, checks it, shows you the card. Or start from
          something you already have.
        </p>
      </header>

      <section className="nc-section">
        <h3 className="nc-h">Start from</h3>
        <div className="nc-ways">
          <div className="nc-way is-primary">
            <span className="nc-way-title">
              <MessageSquareText size={17} strokeWidth={1.9} /> A description
            </span>
            <span className="nc-way-text">Say what you want in the chat. Attach the photos it should use — a person, a product, a place.</span>
            <span className="nc-way-sub">
              Penguin picks free models from its knowledge base <ArrowRight size={12} strokeWidth={2.2} />
            </span>
          </div>
          <button type="button" className="nc-way" onClick={() => onOpenLibrary('templates')}>
            <span className="nc-way-title">
              <LayoutTemplate size={17} strokeWidth={1.9} /> A template
            </span>
            <span className="nc-way-text">A finished, tested setup with its own runbook. Only its settings change.</span>
            <span className="nc-way-sub">Yours and suggested, in the Library</span>
          </button>
          <button type="button" className="nc-way" onClick={() => onOpenLibrary('workflows')}>
            <span className="nc-way-title">
              <Workflow size={17} strokeWidth={1.9} /> A workflow you kept
            </span>
            <span className="nc-way-text">Run again with new inputs — the nodes and wiring stay, only the values change.</span>
            <span className="nc-way-sub">Saved from past creations, in the Library</span>
          </button>
        </div>
      </section>

      <section className="nc-section">
        <h3 className="nc-h">
          Your inputs <span className="nc-h-sub">the files the job will read — add them now or when the design asks</span>
        </h3>
        <ReferenceSlots slots={slots} free={free} disabled={referencesDisabled} onAdd={onAddReference} onOpen={onOpen} onFromLibrary={onFromLibrary} />
      </section>

      <StarterWorkflows client={client} workspaceVersion={workspaceVersion} onRunAgain={onRunAgain} />
    </div>
  )
}

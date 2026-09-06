/* The studio: everything the agent produced this session, beside the conversation instead of
 * buried in its scrollback. Composes the existing WorkflowShelf/ArtifactView with the new
 * telemetry panels; all data is the bridge's own record (useStudioState) plus the artifact
 * stream the window already had.
 */

import { useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact } from '../../agentd/artifacts'
import WorkflowShelf from '../workflows/WorkflowShelf'
import { ActiveRunPanel } from './ActiveRunPanel'
import { FileExplorer } from './FileExplorer'
import { InstancePanel } from './InstancePanel'
import { KpiRow } from './KpiRow'
import { RenderGallery } from './RenderGallery'
import { RunHistory } from './RunHistory'
import { StudioToolbar } from './StudioToolbar'
import { useStudioState } from './useStudioState'

import './studio.css'

const RANGES = [
  { label: 'Today', days: 1 },
  { label: '7 days', days: 7 },
  { label: '30 days', days: 30 },
] as const

export function StudioDashboard({
  client,
  connected,
  running,
  artifacts,
  credits,
  onCredits,
  onNewRun,
  accountInitial,
}: {
  client: AgentdClient | undefined
  connected: boolean
  running: boolean
  /** Everything the agent wrote this session — feeds the shelf and the artifact panel. */
  artifacts: Artifact[]
  credits: number | null
  onCredits: () => void
  onNewRun: () => void
  accountInitial: string
}) {
  const state = useStudioState(client, running)
  const [rangeDays, setRangeDays] = useState<number>(1)
  const [query, setQuery] = useState('')

  return (
    <div className="st-dash">
      <StudioToolbar
        query={query}
        onQuery={setQuery}
        connected={connected}
        onNewRun={onNewRun}
        initial={accountInitial}
      />

      <div className="st-stack">
        <div className="st-head">
          <div>
            <h1>Studio</h1>
            <p>
              Everything the agent has built this session — graphs, renders and files, one
              keystroke from the conversation.
            </p>
          </div>
          <div className="st-seg">
            {RANGES.map((r) => (
              <button
                key={r.days}
                className={rangeDays === r.days ? 'is-on' : ''}
                onClick={() => setRangeDays(r.days)}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <KpiRow state={state} rangeDays={rangeDays} credits={credits} onCredits={onCredits} />

        <div className="st-two-up">
          <RenderGallery renders={state.renders || []} running={running} query={query} />
          <ActiveRunPanel state={state} client={client} />
        </div>

        <div className="st-two-up">
          <section className="st-panel st-shelf-panel">
            <WorkflowShelf artifacts={artifacts} />
          </section>
          <div className="st-side-stack">
            {/* EVERY file, not just the non-media six. The gallery above answers "what did the
                renders look like"; this answers "what is in my workspace" — and the two questions
                want different views of the same set, so this one takes all of them. */}
            <section className="st-panel">
              <FileExplorer artifacts={artifacts} />
            </section>
            <InstancePanel state={state} query={query} client={client} />
          </div>
        </div>

        <RunHistory runs={state.runs || []} rangeDays={rangeDays} query={query} />
      </div>
    </div>
  )
}

/* The studio: what the agent made, beside the conversation that made it.
 *
 * ONE PRIMARY OBJECT PER COLUMN. A rail that lists the workspace, a pane that shows the one thing
 * you picked, and the chat (a sibling column, owned by App). That is the whole screen.
 *
 * WHAT WAS HERE BEFORE, and why it is gone. Fifteen surfaces: a search box, a status chip, a "new
 * run" button, an avatar, a title, a paragraph, a Today/7d/30d switcher, four KPI cards carrying
 * meters and sparklines, a render gallery, an active-run panel, a workflow shelf, a file tree, an
 * instance panel and a run-history table — every one of them boxed in its own card, none of them
 * the file you wanted to read. The dashboard described the work instead of showing it.
 *
 * Nothing that MATTERED was dropped, it moved into the thing it belongs to: the instance (and its
 * GPU/VRAM/model list) is a chip in the top bar, the active run is a strip that exists only while
 * something runs, and workflows and renders alike are simply files in the tree. The KPI figures,
 * the history table and the render grid are gone outright — a grid of renders is a second way to
 * reach files the rail already lists, and it cost a whole mode switch to offer it.
 */

import { useEffect, useMemo } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { GpuWarmup } from './useGpuWarmup'

import type { Artifact } from '../../agentd/artifacts'
import { useApp } from '../../state/store'
import { ActiveRunStrip } from './ActiveRunStrip'
import { FileExplorer } from './FileExplorer'
import { FileViewer } from './FileViewer'
import { StudioTopBar } from './StudioTopBar'
import { useStudioState } from './useStudioState'

import './studio.css'

export function StudioDashboard({
  client,
  gpu,
  running,
  artifacts,
  credits,
  onCredits,
}: {
  client: AgentdClient | undefined
  gpu: GpuWarmup
  running: boolean
  /** Everything the agent wrote this session — the rail's whole content. */
  artifacts: Artifact[]
  credits: number | null
  onCredits: () => void
}) {
  const state = useStudioState(client, running)
  // Selection is STORE state, not local: the rail is not the only thing that picks. A thumbnail in
  // the transcript lands here too — see the note on `selectedArtifactPath`.
  const selectedPath = useApp((s) => s.selectedArtifactPath)
  const setSelectedPath = useApp((s) => s.selectArtifact)

  // SELECT BY PATH, RESOLVE BY LOOKUP. Holding the Artifact object itself would pin a stale copy:
  // the same file is re-declared as later turns touch it (a size arrives, a render finishes), and
  // the pane would keep showing the first version it was handed.
  const selected = useMemo(
    () => artifacts.find((a) => a.path === selectedPath) || null,
    [artifacts, selectedPath],
  )

  // NOTHING OPENS BY ITSELF. The pane is a response to a click and only that — no auto-open, no
  // "helpfully" showing the newest file. Two reasons it must not:
  //
  //   It would make the pane permanent, and a pane that is always there is a second place to look
  //   whether or not you asked for one. The rail plus the conversation is the resting state.
  //
  //   It would break the thumbnail rule. The gallery and the transcript render bounded
  //   `thumbnailUrl` previews precisely so the ORIGINAL bytes are not fetched "until somebody
  //   explicitly opens the file" (agentd/artifacts.ts). This pane shows the original, so opening a
  //   render on the user's behalf would pull a multi-MB image on every finished run.
  //
  // The one thing this DOES do is drop a selection that no longer applies: `artifacts` is this
  // chat's files, so switching conversations can leave a path selected that belongs to another —
  // and the pane would then show a file the rail beside it does not list.
  useEffect(() => {
    if (selectedPath && !artifacts.some((a) => a.path === selectedPath)) setSelectedPath('')
  }, [artifacts, selectedPath, setSelectedPath])

  return (
    <div className="st-dash">
      <StudioTopBar
        state={state}
        client={client}
        gpu={gpu}
        credits={credits}
        onCredits={onCredits}
      />
      <ActiveRunStrip state={state} client={client} />

      <div className="st-body">
        <aside className="st-rail">
          <FileExplorer
            artifacts={artifacts}
            selected={selected}
            onSelect={(a) => setSelectedPath(a.path)}
          />
        </aside>

        {/* THE PANE IS NOT RENDERED WHEN NOTHING IS SELECTED — not rendered empty, absent. An
            empty-state panel still occupies the column and still has to be explained; the rail
            and the conversation simply take the room back until there is something to show. */}
        {selected && (
          <main className="st-view">
            <FileViewer file={selected} onClose={() => setSelectedPath('')} />
          </main>
        )}
      </div>
    </div>
  )
}

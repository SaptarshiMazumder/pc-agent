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
 * something runs, workflows are simply files in the tree, and renders are a mode of the centre
 * pane rather than a panel competing with it. The KPI figures and the history table are gone
 * outright — they were about the dashboard, not about the work.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { FileCode2 } from 'lucide-react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact } from '../../agentd/artifacts'
import { useApp } from '../../state/store'
import { ActiveRunStrip } from './ActiveRunStrip'
import { FileExplorer } from './FileExplorer'
import { FileViewer } from './FileViewer'
import { RenderGallery } from './RenderGallery'
import { StudioTopBar, type StudioMode } from './StudioTopBar'
import { useStudioState } from './useStudioState'

import './studio.css'

export function StudioDashboard({
  client,
  running,
  artifacts,
  credits,
  onCredits,
}: {
  client: AgentdClient | undefined
  running: boolean
  /** Everything the agent wrote this session — the rail's whole content. */
  artifacts: Artifact[]
  credits: number | null
  onCredits: () => void
}) {
  const state = useStudioState(client, running)
  const [mode, setMode] = useState<StudioMode>('files')
  // Selection is STORE state, not local: the rail is not the only thing that picks. A render tile
  // and a thumbnail in the transcript both land here too — see the note on `selectedArtifactPath`.
  const selectedPath = useApp((s) => s.selectedArtifactPath)
  const setSelectedPath = useApp((s) => s.selectArtifact)

  // SELECT BY PATH, RESOLVE BY LOOKUP. Holding the Artifact object itself would pin a stale copy:
  // the same file is re-declared as later turns touch it (a size arrives, a render finishes), and
  // the pane would keep showing the first version it was handed.
  const selected = useMemo(
    () => artifacts.find((a) => a.path === selectedPath) || null,
    [artifacts, selectedPath],
  )

  // Open the newest NON-IMAGE file the moment there is one, so the pane is never an empty box
  // next to a rail that plainly has contents. Only until the user picks for themselves.
  //
  // IMAGES ARE NEVER AUTO-OPENED, and that is the thumbnail rule, not a taste call: the gallery
  // and the artifact strip deliberately render `thumbnailUrl` so the original bytes are not
  // fetched or decoded "until somebody explicitly opens the file" (agentd/artifacts.ts). This
  // pane shows the ORIGINAL, so selecting a render on the user's behalf would pull a multi-MB
  // image on every finished run — quietly undoing that, once per render. A click here is an
  // explicit open and still gets the full-size file; nothing else does.
  //
  // IT ALSO REPAIRS A SELECTION THAT NO LONGER APPLIES. `artifacts` is now this chat's files, so
  // switching conversations can leave a path selected that belongs to a different one — the pane
  // would sit empty next to a rail full of files. A selection that is not in the current list is
  // treated as no selection.
  useEffect(() => {
    if (artifacts.length === 0) return
    if (selectedPath && artifacts.some((a) => a.path === selectedPath)) return
    for (let i = artifacts.length - 1; i >= 0; i--) {
      if (artifacts[i].kind === 'file') {
        setSelectedPath(artifacts[i].path)
        return
      }
    }
    if (selectedPath) setSelectedPath('')
  }, [artifacts, selectedPath, setSelectedPath])

  // PICKING SOMETHING LEAVES THE GRID. A render tile and a transcript thumbnail both set the
  // selection through the store — they are not children of this component and cannot switch the
  // mode themselves — so a click while browsing Renders would otherwise change what the pane
  // WOULD show without changing what it does show, and read as the click having done nothing.
  const previousPath = useRef(selectedPath)
  useEffect(() => {
    const changed = selectedPath && selectedPath !== previousPath.current
    previousPath.current = selectedPath
    if (changed) setMode('files')
  }, [selectedPath])

  return (
    <div className="st-dash">
      <StudioTopBar
        mode={mode}
        onMode={setMode}
        state={state}
        client={client}
        credits={credits}
        onCredits={onCredits}
      />
      <ActiveRunStrip state={state} client={client} />

      <div className="st-body">
        <aside className="st-rail">
          <FileExplorer
            artifacts={artifacts}
            selected={selected}
            onSelect={(a) => {
              setSelectedPath(a.path)
              setMode('files')
            }}
          />
        </aside>

        <main className="st-view">
          {mode === 'renders' ? (
            <RenderGallery renders={state.renders || []} running={running} query="" />
          ) : selected ? (
            <FileViewer file={selected} />
          ) : (
            <div className="st-view-empty">
              <FileCode2 size={26} strokeWidth={1.4} />
              <p>Workflows and renders from this conversation appear here.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

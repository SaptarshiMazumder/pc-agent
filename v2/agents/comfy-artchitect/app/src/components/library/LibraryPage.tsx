/* The Library as a destination of its own, in the rail (creative-studio redesign).
 *
 * THE SAME PANEL, A BIGGER ROOM. Reusable workflows are the product's promise, so the shelf that
 * holds them is one click from anywhere rather than a tab inside a chat's stage. It is the very
 * LibraryPanel the stage shows — upload, use, run again, notes, delete, the chat filter — with
 * the page's own head over it. The stage's Library tab is still there too, because a slot's
 * From Library door opens it in place.
 *
 * "USE IN THIS CHAT" MEANS THE OPEN CHAT, exactly as in the stage: the one the rail has
 * selected. The page takes the person back to it, so the agent's reply is on screen.
 */

import type { AgentdClient } from '@agentd/client'

import type { LibraryItem } from '../../agentd/library'
import type { Slot } from '../../agentd/reference-slots'
import { LibraryPanel } from './LibraryPanel'

export function LibraryPage({
  client,
  sessionKey,
  slots,
  running,
  workspaceVersion,
  onUseWorkflow,
  onRunAgain,
  onUseTemplate,
}: {
  client: AgentdClient | undefined
  /** The open chat — where "Use in this chat" lands. */
  sessionKey: string
  slots: Slot[]
  running: boolean
  workspaceVersion: number
  onUseWorkflow: (item: LibraryItem) => void
  onRunAgain: (item: LibraryItem) => void
  onUseTemplate: (item: LibraryItem) => void
}) {
  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">Library</h1>
          <p className="page-sub">
            Workflows you can run again in one click, and the faces, products and files you keep reusing — shared by every chat.
          </p>
        </div>
      </header>
      <div className="lib-page">
        <LibraryPanel
          client={client}
          sessionKey={sessionKey}
          slots={slots}
          running={running}
          workspaceVersion={workspaceVersion}
          targetRole=""
          onClearTarget={() => {}}
          useLabel="Use in new chat"
          titled={false}
          onUseWorkflow={onUseWorkflow}
          onRunAgain={onRunAgain}
          onUseTemplate={onUseTemplate}
        />
      </div>
    </>
  )
}

export default LibraryPage

/* The Library — ONE place for everything the person keeps, a tab per kind of thing:
 *
 *   Templates       whole setups: theirs (saved from a chat, uploaded) and the app's suggested ones
 *   Workflows       kept from a chat or uploaded — run again, or use in a chat
 *   Inputs          faces, products, clips to reuse — use one in a slot
 *   Saved renders   what Add to Library / Save to Library kept from chats
 *   Uploads         what came from this computer
 *
 * It replaces three doors that led to the same shelf: the Library page, the studio's Library tab
 * and the Templates page. A slot's From Library opens it on Inputs with the role waiting; "Use"
 * on a reference fills that slot and the page hands back to the chat (store.libraryTarget).
 */

import type { AgentdClient } from '@agentd/client'

import type { LibraryItem } from '../../agentd/library'
import type { Slot } from '../../agentd/reference-slots'
import type { LibraryTab } from '../../state/store'
import { SuggestedTemplatesPage } from '../templates/SuggestedTemplatesPage'
import { LibraryPanel, type LibraryShow } from './LibraryPanel'

const TABS: { id: LibraryTab; label: string }[] = [
  { id: 'templates', label: 'Templates' },
  { id: 'workflows', label: 'Workflows' },
  { id: 'inputs', label: 'Inputs' },
  { id: 'renders', label: 'Saved renders' },
  { id: 'uploads', label: 'Uploads' },
]
const SHOW: Record<LibraryTab, LibraryShow> = {
  templates: 'templates',
  workflows: 'workflows',
  inputs: 'references',
  renders: 'renders',
  uploads: 'uploads',
}

export function LibraryPage({
  client,
  sessionKey,
  slots,
  running,
  workspaceVersion,
  tab,
  onTab,
  targetRole,
  onClearTarget,
  useLabel,
  onUseWorkflow,
  onRunAgain,
  onUseTemplate,
}: {
  client: AgentdClient | undefined
  /** The open chat — where "Use in this chat" and a slot's reference land. */
  sessionKey: string
  slots: Slot[]
  running: boolean
  workspaceVersion: number
  tab: LibraryTab
  onTab: (tab: LibraryTab) => void
  /** A slot waiting for a reference (opened from its From Library door), or ''. */
  targetRole: string
  onClearTarget: () => void
  /** The workflow Use button's words: "Use in this chat" when a chat is open, else "Use in new chat". */
  useLabel: string
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
            Everything you keep, shared by every creation: templates, workflows to run again, and the faces, products and files you reuse.
          </p>
        </div>
      </header>
      <div className="lib-tabs" role="tablist" aria-label="Library">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`lib-tab${tab === t.id ? ' on' : ''}`} onClick={() => onTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="lib-page">
        <div className="lib-page-body">
          {tab === 'templates' && <SuggestedTemplatesPage client={client} onUse={onUseTemplate} embedded />}
          <LibraryPanel
            client={client}
            sessionKey={sessionKey}
            slots={slots}
            running={running}
            workspaceVersion={workspaceVersion}
            targetRole={tab === 'inputs' ? targetRole : ''}
            onClearTarget={onClearTarget}
            useLabel={useLabel}
            titled={false}
            show={SHOW[tab]}
            onUseWorkflow={onUseWorkflow}
            onRunAgain={onRunAgain}
            onUseTemplate={onUseTemplate}
          />
        </div>
      </div>
    </>
  )
}

export default LibraryPage

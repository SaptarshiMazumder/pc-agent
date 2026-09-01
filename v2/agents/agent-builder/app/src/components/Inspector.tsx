/* WHAT THIS PANEL IS FOR: watching the files appear as an agent gets built.
 *
 * A conversation is about ONE agent — chosen in the hero, created by the chat, or read back out of
 * a resumed transcript. There is no chooser here: a panel you can re-point mid-chat is a panel
 * that can disagree with the conversation. To work on something else, start a new chat. The whole
 * panel is hidden until an agent is in focus.
 */

import type { AgentdClient } from '@agentd/client'
import { useState } from 'react'
import type { TreeEntry, useAgentFiles } from '../agentd/agent-files'
import type { AgentRow } from '../agentd/roster'
import { FileTree } from './FileTree'
import { FileViewer } from './FileViewer'

export function Inspector({
  agent,
  client,
  files,
  onChanged,
}: {
  agent: AgentRow | null
  client: AgentdClient
  files: ReturnType<typeof useAgentFiles>
  onChanged: () => void
}) {
  const [viewing, setViewing] = useState<TreeEntry | null>(null)

  if (!agent) return null

  const initials = (agent.name || agent.id).slice(0, 2).toUpperCase()

  return (
    <aside className="panel">
      <div className="card panel-head">
        <span className="tile lg" style={agent.color ? { background: agent.color } : undefined}>
          {initials}
        </span>
        <div className="panel-id">
          <h2>{agent.name || agent.id}</h2>
          <p>
            {[agent.tagline || agent.description || agent.id, agent.version && `v${agent.version}`]
              .filter(Boolean)
              .join('  ·  ')}
          </p>
        </div>
      </div>

      {/* THE ACTIONS CARD RETIRED HERE (validate · package · publish). The Ship screen carries
          all three with the same calls and the same two-step publish contract — verified before
          this card came out. One place per verb; two was how they drift. */}

      <div className="card panel-files">
        <div className="card-label">
          <span>Files</span>
          {/* The tree re-reads itself after every tool, but a file can also change from outside
              this window — an editor, another run. This is the way to be sure. */}
          <button className="link-btn" title="Re-read from disk" onClick={() => void files.refresh()}>
            refresh
          </button>
        </div>
        <FileTree rows={files.rows} error={files.error} onToggle={files.toggle} onOpen={setViewing} />
      </div>

      {viewing && <FileViewer entry={viewing} client={client} onClose={() => setViewing(null)} />}
    </aside>
  )
}

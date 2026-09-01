/* The Files pane — the agent's source laid out beside the conversation: tree on the left, the
 * opened file inline on the right, raw with a line-number gutter, the way source reads.
 *
 * ONE TREE, ONE READER. The tree is the same FileTree the inspector draws, fed by the same
 * useAgentFiles result App already holds (refresh, fresh-file flash and the tool-tick re-read
 * included); the content comes through useFileBody, the same rules the modal viewer applies —
 * media plays, a big binary is refused, never dumped. Nothing here reads a file a second way.
 *
 * RAW, NOT RENDERED, for text — including markdown. The modal answers "what does this file say";
 * this pane answers "what is IN this file", which for source means the actual bytes with line
 * numbers. The modal (and its rendered markdown) is still one click away in the inspector.
 *
 * WHAT THE DESIGN DRAWS THAT IS NOT HERE, and why: the History and Edit buttons (no mechanism —
 * nothing records file history, and in-app editing is a feature decision, not a restyle), the
 * amber changed-line tinting and the Changes panel with Revert (both need per-session change
 * tracking that does not exist). They arrive with their mechanisms or not at all.
 */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import type { TreeEntry, useAgentFiles } from '../agentd/agent-files'
import type { AgentRow } from '../agentd/roster'
import { FileTree } from './FileTree'
import { useFileBody } from './FileViewer'

/** The inline reader — split out so it only mounts (and fetches) when a file is open. */
function InlineFile({ entry, client }: { entry: TreeEntry; client: AgentdClient }) {
  const { body, url, media } = useFileBody(entry, client)

  return (
    <div className="wsf-view">
      <div className="wsf-view-head">
        {/* The AGENT-relative path — the absolute one names this machine's account layout,
            which is the daemon's business, not the pane's. The full path stays in the title. */}
        <code className="wsf-view-name" title={entry.path}>
          {entry.rel || entry.name}
        </code>
      </div>
      <div className="wsf-view-body">
        {entry.kind === 'image' && <img src={url} alt={entry.name} />}
        {entry.kind === 'video' && <video src={url} controls />}
        {entry.kind === 'audio' && <audio src={url} controls />}
        {!media && body.state !== 'loading' && body.state !== 'note' && (
          <div className="wsf-code">
            {body.text.split('\n').map((line, i) => (
              <div className="wsf-line" key={i}>
                <span className="wsf-ln">{i + 1}</span>
                <span className="wsf-lc">{line || ' '}</span>
              </div>
            ))}
          </div>
        )}
        {!media && (body.state === 'loading' || body.state === 'note') && (
          <div className="tree-empty">{body.text}</div>
        )}
      </div>
    </div>
  )
}

export function WorkspaceFiles({
  client,
  agent,
  files,
}: {
  client: AgentdClient
  agent: AgentRow
  files: ReturnType<typeof useAgentFiles>
}) {
  const [open, setOpen] = useState<TreeEntry | null>(null)

  // A new subject means none of these paths exist anymore; holding the old file open would
  // show one agent's source under another agent's header.
  useEffect(() => setOpen(null), [agent.id])

  return (
    <div className="wsf">
      <div className="wsf-tree">
        <FileTree
          rows={files.rows}
          error={files.error}
          onToggle={files.toggle}
          onOpen={setOpen}
        />
      </div>
      {open ? (
        <InlineFile entry={open} client={client} />
      ) : (
        <div className="wsf-view wsf-view--empty">
          <div className="tree-empty">pick a file</div>
        </div>
      )}
    </div>
  )
}

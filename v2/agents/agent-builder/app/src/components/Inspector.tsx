/* WHAT THIS PANEL IS FOR: watching the files appear as an agent gets built.
 *
 * A conversation is about ONE agent, decided when it starts — in the hero, or by the agent that
 * chat just created. There is no chooser here: a panel you can re-point mid-chat is a panel that
 * can disagree with the conversation. To work on something else, start a new chat. The whole
 * panel is hidden until an agent is in focus.
 */

import { resultText, type AgentdClient } from '@agentd/client'
import { useState } from 'react'
import type { TreeEntry, useAgentFiles } from '../agentd/agent-files'
import { publishable, publishBlockReason, type AgentRow } from '../agentd/roster'
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
  const [out, setOut] = useState<{ title: string; text: string; bad: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [viewing, setViewing] = useState<TreeEntry | null>(null)

  const canPublish = publishable(agent)

  async function runTool(tool: string, title: string, extra: Record<string, unknown> = {}) {
    if (!agent) return null
    setOut({ title, text: `running ${tool} on ${agent.id}…`, bad: false })
    setBusy(true)
    try {
      const res = await client.invokeTool(tool, { agent_id: agent.id, ...extra })
      const text = resultText(res) || '(no output)'
      setOut({ title, text, bad: false })
      onChanged()
      return text
    } catch (e) {
      // The daemon throws with the tool's own report text when a tool reports an error — that IS
      // the result, so show it rather than a generic failure line.
      setOut({ title, text: String((e as Error)?.message || e), bad: true })
      return null
    } finally {
      setBusy(false)
    }
  }

  /* Publish is TWO steps on purpose.
     A publish uploads a public artifact and rewrites the registry index every client reads, so one
     click must not be enough. The first call is the tool's default dry run: it prints the exact
     index that would be published — which is where you notice a bundle you did not expect is about
     to change. Only then do we ask, and only a yes sends dry_run=false + confirm=true (the tool
     requires BOTH, so nothing here can publish by accident either).
     A dry run that FAILED returns null — usually "not configured to publish", already on screen —
     and we stop rather than asking the user to confirm something that cannot work. */
  async function publishFlow() {
    if (!agent || !canPublish) return // the button is disabled; this guards a stale handler
    const preview = await runTool('publish_agent', 'Publish — preview', { dry_run: true })
    if (preview === null) return
    const ok = window.confirm(
      `Publish ${agent.id} to the marketplace?\n\n` +
        'This uploads a PUBLIC artifact and rewrites the registry index.\n' +
        'Check the preview behind this dialog first — it lists every bundle that will be in the ' +
        'published index.',
    )
    if (!ok) {
      setOut({ title: 'Publish', text: `cancelled — nothing was uploaded.\n\n${preview}`, bad: false })
      return
    }
    await runTool('publish_agent', 'Publish', { dry_run: false, confirm: true })
  }

  if (!agent) return null

  return (
    <aside className="panel glass" id="panel">
      <div className="panel-head">
        <span className="panel-label">Working on</span>
        <h2 className="panel-agent">{agent.name || agent.id}</h2>
        <p className="panel-sub">
          {[agent.tagline || agent.description || agent.id, agent.version && `v${agent.version}`]
            .filter(Boolean)
            .join('  ·  ')}
        </p>
      </div>

      <div className="panel-actions">
        <button
          className="ghost-btn sm"
          disabled={busy}
          title="Check this agent for problems the daemon will not report"
          onClick={() => void runTool('validate_agent', 'Validation')}
        >
          Validate
        </button>
        <button
          className="ghost-btn sm"
          disabled={busy}
          title="Build the shareable .agentpkg"
          onClick={() => void runTool('package_agent', 'Package')}
        >
          Package
        </button>
        <button
          className="prime-btn sm"
          disabled={busy || !canPublish}
          title={
            canPublish
              ? 'Publish to the marketplace — shows a preview first, then asks to confirm'
              : publishBlockReason(agent)
          }
          onClick={() => void publishFlow()}
        >
          Publish
        </button>
      </div>

      <div className="tree-head">
        <span className="rail-label">Files</span>
        {/* The tree re-reads itself after every tool, but a file can also change from outside this
            window — an editor, another run. This is the way to be sure. */}
        <button className="link-btn" title="Re-read from disk" onClick={() => void files.refresh()}>
          refresh
        </button>
      </div>

      <FileTree rows={files.rows} error={files.error} onToggle={files.toggle} onOpen={setViewing} />

      {out && (
        <div className="panel-out">
          <div className="panel-out-head">
            <span>{out.title}</span>
            <button className="link-btn" onClick={() => setOut(null)}>
              clear
            </button>
          </div>
          <pre className={out.bad ? 'bad' : ''}>{out.text}</pre>
        </div>
      )}

      {viewing && <FileViewer entry={viewing} client={client} onClose={() => setViewing(null)} />}
    </aside>
  )
}

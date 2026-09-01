/* The Capabilities pane — what this agent may reach, as rows, beside the file that says so.
 *
 * EVERYTHING SHOWN IS READ FROM THE REAL agent.toml — the same file, fetched the same way the
 * Files pane fetches it. The rows are a reading aid for the TOML on the right, not a second
 * source of truth: `allow` entries become granted rows, `deny` entries become denied rows
 * (deny always wins, so they render as refusals, not toggles), `heartbeat` becomes the
 * Schedule row. No [tools] table means the agent sees the whole catalog, and the pane says
 * exactly that rather than inventing a list.
 *
 * THE TOGGLES ARE INERT, deliberately. Flipping one would write agent.toml and reload the
 * agent — machinery that exists (the daemon can do both) but whose wiring changes what a click
 * DOES, which is a feature decision parked by agreement, not a styling one. Until then the
 * switches render the state and refuse the flip, saying why in their title. The way to change
 * a grant remains the conversation.
 *
 * THE PARSE IS DELIBERATELY NARROW: the simple `key = [ "…" ]` shapes create_agent writes and
 * agents' own tomls use. Anything it cannot read leaves the rows unrendered and says so —
 * the raw file on the right is always the truth, so a failed parse costs a summary, never
 * a lie. Skills cards are absent for now: their names and purposes live in each skill's own
 * SKILL.md, which is another fetch and another parse — they join when read for real.
 */

import { Clock, Globe, Shield, ShieldOff, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import type { AgentdClient } from '@agentd/client'
import type { useAgentFiles } from '../agentd/agent-files'
import type { AgentRow } from '../agentd/roster'

/** The narrow parse — see the header. Returns null when the file defies it. */
export function parseCaps(toml: string): { allow: string[]; deny: string[]; heartbeat: string } | null {
  try {
    const list = (key: string): string[] | null => {
      const m = toml.match(new RegExp(`^\\s*${key}\\s*=\\s*\\[([\\s\\S]*?)\\]`, 'm'))
      if (!m) return null
      return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])
    }
    const heartbeat = toml.match(/^\s*heartbeat\s*=\s*"([^"]*)"/m)?.[1] || ''
    return { allow: list('allow') || [], deny: list('deny') || [], heartbeat }
  } catch {
    return null
  }
}

const TOOL_ICONS: Record<string, ReactNode> = {
  web_search: <Globe size={16} />,
  web_fetch: <Globe size={16} />,
}

export function WorkspaceCapabilities({
  client,
  agent,
  files,
}: {
  client: AgentdClient
  agent: AgentRow
  files: ReturnType<typeof useAgentFiles>
}) {
  const [toml, setToml] = useState<{ state: 'loading' | 'ok' | 'note'; text: string }>({
    state: 'loading',
    text: 'loading…',
  })

  const entry = files.rows.find((r) => r.depth === 0 && r.name === 'agent.toml')

  useEffect(() => {
    if (!entry) {
      setToml({ state: 'note', text: 'no agent.toml in the tree yet' })
      return
    }
    let live = true
    setToml({ state: 'loading', text: 'loading…' })
    void (async () => {
      try {
        const text = await (await fetch(client.fileUrl(entry.path))).text()
        if (live) setToml({ state: 'ok', text })
      } catch (e) {
        if (live) setToml({ state: 'note', text: `could not read agent.toml: ${String((e as Error)?.message || e)}` })
      }
    })()
    return () => {
      live = false
    }
    // keyed on the path AND the tool tick the caller's tree already tracks: a tool that edited
    // agent.toml refreshes the rows the same way it refreshes the tree.
  }, [client, entry?.path, files.rows])

  const caps = toml.state === 'ok' ? parseCaps(toml.text) : null
  const inert = 'Not wired yet — change grants in the conversation; the agent edits its own agent.toml'

  return (
    <div className="wsc">
      <div className="wsc-main">
        <h3 className="wsc-head">Tools</h3>
        {toml.state !== 'ok' ? (
          <p className="lp-side-empty">{toml.text}</p>
        ) : caps === null ? (
          <p className="lp-side-empty">could not summarise this agent.toml — the file on the right is the truth</p>
        ) : (
          <div className="wsc-rows">
            {caps.allow.length === 0 && (
              <div className="wsc-row">
                <Wrench size={16} />
                <span className="wsc-row-text">
                  <span className="wsc-row-name">Every shared tool</span>
                  <span className="wsc-row-note">no [tools] allow list — this agent sees the whole catalog</span>
                </span>
              </div>
            )}
            {caps.allow.map((t) => (
              <div className="wsc-row" key={`a-${t}`}>
                {TOOL_ICONS[t] || <Wrench size={16} />}
                <span className="wsc-row-text">
                  <span className="wsc-row-name">{t}</span>
                </span>
                <span
                  className="wsc-toggle on"
                  role="switch"
                  aria-checked="true"
                  aria-disabled="true"
                  title={inert}
                />
              </div>
            ))}
            {caps.deny.map((t) => (
              <div className="wsc-row wsc-row--denied" key={`d-${t}`}>
                <ShieldOff size={16} />
                <span className="wsc-row-text">
                  <span className="wsc-row-name">{t}</span>
                  <span className="wsc-row-note">deny always wins</span>
                </span>
                <span className="wsc-denied">
                  <Shield size={12} />
                  denied
                </span>
              </div>
            ))}
          </div>
        )}

        <h3 className="wsc-head">Schedule</h3>
        <div className="wsc-rows">
          <div className="wsc-row">
            <Clock size={16} />
            <span className="wsc-row-text">
              <span className="wsc-row-name">
                {caps?.heartbeat ? `Wakes every ${caps.heartbeat}` : 'Never wakes itself'}
              </span>
              <span className="wsc-row-note">
                {caps?.heartbeat
                  ? 'heartbeat — runs its HEARTBEAT.md checklist'
                  : 'turn on a heartbeat in the conversation for a HEARTBEAT.md checklist'}
              </span>
            </span>
            <span
              className={`wsc-toggle ${caps?.heartbeat ? 'on' : ''}`}
              role="switch"
              aria-checked={!!caps?.heartbeat}
              aria-disabled="true"
              title={inert}
            />
          </div>
        </div>
      </div>

      <aside className="wsc-toml">
        <div className="wsf-view-head">
          <code className="wsf-view-name" title={entry?.path}>
            agent.toml
          </code>
        </div>
        <div className="wsf-view-body">
          {toml.state === 'ok' ? (
            <div className="wsf-code">
              {toml.text.split('\n').map((line, i) => (
                <div className="wsf-line" key={i}>
                  <span className="wsf-ln">{i + 1}</span>
                  <span className="wsf-lc">{line || ' '}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="tree-empty">{toml.text}</div>
          )}
        </div>
      </aside>
    </div>
  )
}

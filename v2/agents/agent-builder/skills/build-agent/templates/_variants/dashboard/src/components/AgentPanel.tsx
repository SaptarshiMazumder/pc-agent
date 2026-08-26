/* The agent, in a panel — always beside the work instead of being a separate screen.
 *
 * A TEMPLATE FILE. The thread rendering, the run plumbing and the store are all the base
 * skeleton's; this only arranges them into a column: a header, the conversation, and a compact
 * composer. The full-width chat view is gone from this template on purpose — a workbench where
 * the agent lives NEXT to the content never makes you choose between looking at the work and
 * talking about it.
 *
 * THE COMPOSER HERE IS DELIBERATELY SMALL — text and send, running state, stop. Attachments,
 * credits and the context meter belong to the full composer; a side panel that grew all of that
 * would be the chat view again, just narrower. Add back exactly what your agent needs.
 */

import { useState } from 'react'
import { MessageSquarePlus, Square } from 'lucide-react'
import type { AgentdClient } from '@agentd/client'

import { useRun } from '../agentd/run'
import { useApp, useSession } from '../state/store'
import { Thread } from './Thread'

export default function AgentPanel({ client, connected }: { client: AgentdClient | null; connected: boolean }) {
  const session = useSession()
  const newSession = useApp((s) => s.newSession)
  const { send, abort } = useRun(client)
  const [text, setText] = useState('')

  const submit = () => {
    const body = text.trim()
    if (!body || session.running) return
    setText('')
    void send(body)
  }

  return (
    <aside className="agent-panel">
      <header className="agent-panel-head">
        <span className="agent-panel-title">Agent</span>
        {/* A fresh conversation WITHOUT leaving the screen — show=false keeps the view. */}
        <button className="dash-refresh" title="New conversation" onClick={() => newSession(false)}>
          <MessageSquarePlus size={15} />
        </button>
      </header>

      <div className="agent-panel-thread">
        <Thread items={session.items} running={session.running} />
      </div>

      <div className="agent-panel-composer">
        <textarea
          className="agent-panel-input"
          value={text}
          rows={1}
          placeholder={connected ? 'Ask the agent…' : 'connecting…'}
          disabled={!connected}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        {session.running ? (
          <button className="agent-panel-send" title="Stop" onClick={() => void abort()}>
            <Square size={14} />
          </button>
        ) : (
          <button className="agent-panel-send" title="Send" disabled={!connected || !text.trim()} onClick={submit}>
            ↑
          </button>
        )}
      </div>
    </aside>
  )
}

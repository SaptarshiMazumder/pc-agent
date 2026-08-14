/* The empty state.
 *
 * Which agent this conversation is ABOUT is decided BEFORE it starts. Inferring it from prose is
 * how three attempts at one agent produced one agent and an argument about overwriting.
 *
 * The picker is offered ONLY when there is something to open. On a fresh install the user has
 * nothing but Agent Builder itself, and asking "existing or new?" with one meaningless answer is
 * ceremony — so the whole block stays hidden and the chat behaves exactly as before.
 */

import { useState } from 'react'
import type { AgentRow } from '../agentd/roster'

// Starter prompts. NEVER name a specific agent — these are clickable, and on a machine that never
// had one, the click asks to work on something that does not exist.
const SUGGESTIONS = [
  'Build an agent that summarises my YouTube history and charts it by month',
  'Give one of my agents its own app window',
  'Validate every agent I have and tell me what is wrong',
]

export function Hero({
  agents,
  onOpenAgent,
  onSuggest,
}: {
  agents: AgentRow[]
  onOpenAgent: (id: string) => void
  onSuggest: (text: string) => void
}) {
  const [picked, setPicked] = useState('')
  const choice = picked || agents[0]?.id || ''

  return (
    <div className="hero">
      <h1>What should we build?</h1>
      <p>
        Describe an agent — what it does, what it needs access to — and I'll write it, check it,
        and make it shippable.
      </p>

      {agents.length > 0 && (
        <div className="pick-scope">
          <div className="pick-row">
            <span className="pick-label">Work on an existing agent</span>
            <select value={choice} onChange={(e) => setPicked(e.target.value)}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {/* A catalogue agent (mine === false) is still openable — chat, files, validate
                      — it just is not the user's to publish. Said here, in the list, so the greyed
                      Publish button is never the first time they find out. */}
                  {(a.name || a.id) + (a.mine === false ? ' · catalogue' : '')}
                </option>
              ))}
            </select>
            <button className="ghost-btn sm" onClick={() => choice && onOpenAgent(choice)}>
              Open
            </button>
          </div>
          <div className="pick-or">
            <span>or describe something new below</span>
          </div>
        </div>
      )}

      <div className="suggests">
        {SUGGESTIONS.map((s) => (
          <button className="suggest" key={s} onClick={() => onSuggest(s)}>
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

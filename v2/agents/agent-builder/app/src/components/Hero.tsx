/* The empty state: the two ways a conversation can start.
 *
 * The question itself ("What should we build?") is the page header now, so this card does not
 * repeat it — it offers the choices underneath.
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
  {
    title: 'Summarise my YouTube history',
    body: 'Build an agent that summarises my YouTube history and charts it by month',
  },
  {
    title: 'Give an agent its own window',
    body: 'Give one of my agents its own app window',
  },
  {
    title: 'Check everything I have built',
    body: 'Validate every agent I have and tell me what is wrong',
  },
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
      {agents.length > 0 && (
        <div className="card">
          <div className="card-label">
            <span>Work on an existing agent</span>
          </div>
          <div className="pick-row">
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
            <button className="ghost-btn" onClick={() => choice && onOpenAgent(choice)}>
              Open
            </button>
          </div>
          <p className="card-note">
            Opens its files in the inspector and tells the model what it is looking at.
          </p>
        </div>
      )}

      <div className="card">
        <div className="card-label">
          <span>{agents.length > 0 ? 'Or start something new' : 'Start here'}</span>
        </div>
        <div className="suggests">
          {SUGGESTIONS.map((s) => (
            <button className="suggest" key={s.title} onClick={() => onSuggest(s.body)}>
              <span className="check">↗</span>
              <span className="suggest-text">
                <span className="suggest-title">{s.title}</span>
                <span className="suggest-body">{s.body}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

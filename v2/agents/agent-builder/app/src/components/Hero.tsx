/* The empty state: the two ways a conversation can start.
 *
 * SPLIT IN TWO so the composer can sit BETWEEN them. agentd centres its input in an empty page
 * with the greeting above and the suggestions below, and this window kept its composer pinned to
 * the bottom whether there was a conversation or not. The halves are exported separately rather
 * than taking the composer as a child, because the layout that interleaves them belongs to the
 * screen (App), not to either half.
 *
 * The question itself ("What should we build?") is the page header, so these cards do not
 * repeat it — they offer the choices underneath.
 *
 * BOTH PATHS GO THROUGH THE START DIALOG, including the suggestions. A suggestion used to send its
 * prose straight into an empty chat, which meant the one route most likely to be taken by somebody
 * new was also the one route that skipped the window question — and an agent built without that
 * answer gets whatever the model felt like. The suggestion is still what you want built; the
 * dialog is still how it gets built.
 *
 * The "work on an existing agent" card is offered ONLY when there is something to open. On a fresh
 * install the user has nothing but Agent Builder itself, and asking "existing or new?" with one
 * meaningless answer is ceremony.
 */

// Starter prompts. NEVER name a specific agent — these are clickable, and on a machine that never
// had one, the click asks to work on something that does not exist.
const SUGGESTIONS = [
  {
    title: 'Summarise my YouTube history',
    body: 'Build an agent that summarises my YouTube history and charts it by month',
  },
  {
    title: 'Watch a folder and file things',
    body: 'Build an agent that watches a folder and sorts what lands in it',
  },
  {
    title: 'Check everything I have built',
    body: 'Validate every agent I have and tell me what is wrong',
  },
]

export function HeroStart({
  hasAgents,
  onCreate,
  onEdit,
}: {
  /** Is there anything to edit? Drives whether the second button appears at all. */
  hasAgents: boolean
  onCreate: () => void
  onEdit: () => void
}) {
  return (
      <div className="card hero-card">
        <div className="card-label">
          <span>Start</span>
        </div>
        <div className="hero-actions">
          <button className="primary-btn" onClick={onCreate}>
            Create a new agent
          </button>
          {hasAgents && (
            <button className="ghost-btn" onClick={onEdit}>
              Edit an agent
            </button>
          )}
        </div>
        <p className="card-note">
          {hasAgents
            ? 'Editing opens the agent’s files in the inspector and tells the model what it is looking at.'
            : 'You will be asked one question — whether it needs a window — and then we start building.'}
        </p>
      </div>
  )
}

export function HeroSuggestions({
  onSuggest,
}: {
  /** A starter prompt — opens the create dialog carrying this as the opening message. */
  onSuggest: (text: string) => void
}) {
  return (
      <div className="card hero-card">
        <div className="card-label">
          <span>Or start from one of these</span>
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
  )
}

/* How a conversation starts — the two questions asked BEFORE the chat, not inside it.
 *
 * WHY NOT JUST TYPE IT. Agent Builder opened on an empty composer, so every conversation began by
 * guessing what the user meant: new agent or existing one, with a window or without. Inferring
 * that from prose is how three attempts at one agent produced one agent and an argument about
 * overwriting, and how an agent that was meant to have no window got one anyway.
 *
 * Two things are decided here and nowhere else:
 *
 *   CREATE  does the new agent get a window of its own?
 *   EDIT    which existing agent are we working on?
 *
 * Both are questions with a small, knowable set of answers, and both are cheap to ask and
 * expensive to get wrong. Everything else — what the agent should DO — stays in the conversation,
 * where it can be argued with.
 *
 * DELIBERATELY NOT A NAME FIELD. Naming a thing before describing it is the wrong order, and the
 * model picks a better id from the description than the user picks from a blank box.
 */

import { useEffect, useState } from 'react'
import type { AgentRow } from '../agentd/roster'

export type StartMode = 'create' | 'edit'

export function StartModal({
  mode,
  agents,
  seed,
  onCreate,
  onEdit,
  onClose,
}: {
  mode: StartMode
  /** Openable agents only — Agent Builder itself is not one of its own subjects. */
  agents: AgentRow[]
  /** A starter prompt the user clicked, sent as the opening message once the window question is
   *  answered. The suggestion is what they want built; this dialog is still how it gets built. */
  seed?: string
  onCreate: (window: boolean, seed?: string) => void
  onEdit: (id: string) => void
  onClose: () => void
}) {
  const [picked, setPicked] = useState('')
  const choice = picked || agents[0]?.id || ''

  // Escape closes. A dialog that can only be dismissed by finding the right button is a dialog
  // that traps somebody who opened it by accident.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-back" onClick={onClose}>
      <div
        className="modal start-modal"
        role="dialog"
        aria-modal="true"
        aria-label={mode === 'create' ? 'Create a new agent' : 'Edit an agent'}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2>{mode === 'create' ? 'Create a new agent' : 'Edit an agent'}</h2>
          <button className="icon-btn" onClick={onClose} title="Close">
            ✕
          </button>
        </header>

        {mode === 'create' ? (
          <>
            <p className="modal-note">
              {seed
                ? 'One question first, then we start building.'
                : 'Does it need a window of its own?'}
            </p>
            <div className="choice-grid">
              <button className="choice" onClick={() => onCreate(true, seed)}>
                <span className="choice-title">With its own window</span>
                <span className="choice-body">
                  A page of its own that you design — a dashboard, a workbench, a chat. Opens from
                  agentd, or from the button beside the composer here.
                </span>
              </button>
              <button className="choice" onClick={() => onCreate(false, seed)}>
                <span className="choice-title">No window</span>
                <span className="choice-body">
                  Used from the agentd window like any other agent. Right for anything whose
                  answer is a conversation, a file, or a scheduled job.
                </span>
              </button>
            </div>
            <p className="modal-foot">You can add a window later — this only decides where to start.</p>
          </>
        ) : agents.length === 0 ? (
          // The honest empty state. Offering a picker with nothing in it is how a user concludes
          // the feature is broken rather than that they have not built anything yet.
          <p className="modal-note">
            You have not built any agents yet. Create one first — this list fills itself in.
          </p>
        ) : (
          <>
            <p className="modal-note">
              Its files open in the inspector, and the model is told what it is looking at.
            </p>
            <div className="pick-list">
              {agents.map((a) => (
                <button
                  key={a.id}
                  className={'pick-row-btn' + (a.id === choice ? ' is-picked' : '')}
                  onClick={() => setPicked(a.id)}
                  onDoubleClick={() => onEdit(a.id)}
                >
                  <span className="pick-name">{a.name || a.id}</span>
                  {/* A catalogue agent is fully openable — chat, files, validate — it just is not
                      the user's to publish. Said here so the greyed Publish button later is never
                      the first time they find out. */}
                  {a.mine === false && <span className="pick-tag">catalogue</span>}
                  {a.app?.url && <span className="pick-tag">window</span>}
                </button>
              ))}
            </div>
            <div className="modal-actions">
              <button className="ghost-btn" onClick={onClose}>
                Cancel
              </button>
              <button className="primary-btn" disabled={!choice} onClick={() => onEdit(choice)}>
                Open
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

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
 * IT ASKS FOR A NAME NOW, and it deliberately did not before. The old reasoning was that naming
 * a thing before describing it is the wrong order, and that the model picks a better id from a
 * description than a user picks from a blank box. Both are still true — and both were outweighed.
 *
 * The agent is CREATED HERE, from this dialog, before a word is typed. That is what buys the
 * structural guarantee: a windowed agent exists with a complete, working window — sign-in,
 * credits, settings, organizations — from the moment it exists at all, rather than from whenever
 * the model got around to scaffolding one. Something has to be called something to be created, so
 * the name is the price of the guarantee.
 *
 * The name is not final. It is a display name and an id; the conversation that follows writes
 * everything else, and renaming is a `write` away.
 */

import { useEffect, useState } from 'react'
import type { AgentRow } from '../agentd/roster'

export type StartMode = 'create' | 'edit'

/* THE TEMPLATES ON OFFER — module-scope and exported because two surfaces now draw them: this
   dialog's gallery and the launchpad's shelf. One entry per folder in `templates/_variants/`
   (id must match the folder name); adding a template there means adding its card here and
   BOTH surfaces grow it. `blurb` is the shelf's one-liner; `body` is the gallery's fuller
   sentence — same fact at two lengths, kept together so they cannot disagree. */
export const TEMPLATES: { id: string; label: string; blurb: string; body: string }[] = [
  {
    id: 'chat',
    label: 'Chat app',
    blurb: 'A thread and a composer.',
    body:
      'A thread and a composer — for an agent whose work is a conversation. ' +
      'Sign-in, credits, settings and organizations included and working.',
  },
  {
    id: 'dashboard',
    label: 'Dashboard app',
    blurb: 'Sections, panels, and the agent alongside.',
    body:
      'A workbench: sections in the rail, content panels in the middle, and the agent in ' +
      'a permanent chat panel beside the work.',
  },
]

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
  onCreate: (name: string, window: boolean, template: string, seed?: string) => void
  onEdit: (id: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [picked, setPicked] = useState('')
  const named = name.trim()

  /* THE WIZARD. `CREATE_STEPS` is the whole flow, in order — adding a screen later is one entry
     here and one branch below, nothing else moves. Only the CREATE side steps; Edit stays a
     single picker. */
  type CreateStep = 'basics' | 'template'
  const [step, setStep] = useState<CreateStep>('basics')
  const [tpl, setTpl] = useState<string>('chat')
  /** Open in the full-screen preview, or ''. Separate from `tpl` (the selection): looking at one
   *  template must not silently change which one "Use" would take. */
  const [previewing, setPreviewing] = useState('')

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

        {mode === 'create' && step === 'template' ? (
          /* SCREEN 2, GALLERY-SHAPED. Big visual cards — each thumbnail IS the template, the
             real compiled app served by the daemon and scaled down, so the imagery can never
             drift from what ships. Hover a card for its two actions; Preview opens it full
             screen (see below), Use commits. */
          <>
            <p className="modal-note">Pick the shape “{named}” starts as. Everything is editable afterwards.</p>
            <div className="tpl-gallery">
              {TEMPLATES.map((t) => (
                <div key={t.id} className={'tpl-tile' + (tpl === t.id ? ' is-picked' : '')}>
                  <div className="tpl-thumb" onClick={() => setTpl(t.id)}>
                    <iframe
                      className="tpl-thumb-frame"
                      src={`/template-previews/${t.id}/`}
                      title={`${t.label} thumbnail`}
                      tabIndex={-1}
                    />
                    <div className="tpl-hover">
                      <button
                        className="primary-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          setTpl(t.id)
                          onCreate(named, true, t.id, seed)
                        }}
                      >
                        Use
                      </button>
                      <button
                        className="ghost-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          setPreviewing(t.id)
                        }}
                      >
                        Preview
                      </button>
                    </div>
                  </div>
                  <div className="tpl-caption">
                    <span className="tpl-name">{t.label}</span>
                    <span className="tpl-desc">{t.body}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="modal-actions">
              <button className="ghost-btn" onClick={() => setStep('basics')}>
                Back
              </button>
              <button className="primary-btn" onClick={() => onCreate(named, true, tpl, seed)}>
                Use this template
              </button>
            </div>

            {/* FULL-SCREEN PREVIEW — the template at real size, live. Interactive on purpose:
                walking the rail and the screens is the whole point of previewing. The daemon
                connection inside it shows its honest connecting state; layout, styling and every
                screen are exactly what an agent created from it gets. */}
            {previewing && (
              <div className="tpl-full" role="dialog" aria-label="Template preview">
                <header className="tpl-full-head">
                  <span className="tpl-full-title">
                    Template preview — {TEMPLATES.find((t) => t.id === previewing)?.label}
                  </span>
                  <span className="tpl-full-actions">
                    <button
                      className="primary-btn"
                      onClick={() => onCreate(named, true, previewing, seed)}
                    >
                      Use this template
                    </button>
                    <button className="icon-btn" title="Close" onClick={() => setPreviewing('')}>
                      ✕
                    </button>
                  </span>
                </header>
                <iframe
                  className="tpl-full-frame"
                  src={`/template-previews/${previewing}/`}
                  title="template preview"
                />
              </div>
            )}
          </>
        ) : mode === 'create' ? (
          <>
            <p className="modal-note">
              {seed
                ? 'Two questions first, then we start building.'
                : 'What should it be called, and does it need a window of its own?'}
            </p>

            <label className="start-name">
              <span className="field-label">Name</span>
              <input
                autoFocus
                value={name}
                placeholder="Recipe Box"
                onChange={(e) => setName(e.target.value)}
                /* Enter takes the recommended answer. A dialog with one text field and two
                   buttons should not need the mouse. */
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && named) setStep('template')
                }}
              />
            </label>

            {/* DISABLED, NOT HIDDEN, until it has a name: a button that appears when you type is
                a button you did not know was coming.

                SCREEN 1 DECIDES ONE THING: window or not. "With its own window" ADVANCES to the
                template gallery rather than creating — which shape it is deserves its own screen
                with a preview, not a guess made here. "No window" creates immediately; there is
                nothing more to choose. */}
            <div className="choice-grid">
              <button className="choice" disabled={!named} onClick={() => setStep('template')}>
                <span className="choice-title">With its own window</span>
                <span className="choice-body">
                  A page of its own — next you pick its shape from the templates. It starts as a
                  working app with sign-in, credits, settings and organizations already in it.
                </span>
              </button>
              <button className="choice" disabled={!named} onClick={() => onCreate(named, false, 'chat', seed)}>
                <span className="choice-title">No window</span>
                <span className="choice-body">
                  Used from the agentd window like any other agent. Right for anything whose
                  answer is a conversation, a file, or a scheduled job.
                </span>
              </button>
            </div>
            <p className="modal-foot">
              The agent is created now, so there is something to work on. You can rename it, and
              add a window later, at any point.
            </p>
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

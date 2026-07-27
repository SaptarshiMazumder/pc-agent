import { FormEvent, useState } from 'react'
import { X, Sparkles } from 'lucide-react'

import { useApp } from '../state/store'

/**
 * Create-agent dialog. Name is required; description + identity are optional.
 * On submit it calls agents.create (server scaffolds the definition, assigns a
 * unique colour + tagline, loads it live) then selects the new agent. The colour
 * and starter suggestions fill in a beat later via the agents.changed broadcast.
 */
export default function NewAgentModal({ onClose }: { onClose: () => void }) {
  const createAgent = useApp((s) => s.createAgent)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [identity, setIdentity] = useState('')
  // '' = a plain chat agent; 'browser'/'window' also scaffold an app UI (agents/<id>/ui/)
  // declaring how it opens — a browser tab or its own window (like a program)
  const [appMode, setAppMode] = useState<'' | 'browser' | 'window'>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!name.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      await createAgent({
        name: name.trim(),
        description: description.trim(),
        identity: identity.trim(),
        app: appMode
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <span className="modal-title"><Sparkles size={17} /> New agent</span>
          <button type="button" className="icon-btn icon-btn--sm" title="close" onClick={onClose}>
            <X size={17} />
          </button>
        </div>

        <label className="field">
          <span className="field-label">Name</span>
          <input
            className="field-input"
            autoFocus
            value={name}
            placeholder="e.g. Travel Planner"
            onChange={(e) => setName(e.target.value)}
          />
        </label>

        <label className="field">
          <span className="field-label">Description <span className="field-opt">optional</span></span>
          <input
            className="field-input"
            value={description}
            placeholder="one line — what this agent is for"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <label className="field">
          <span className="field-label">Identity <span className="field-opt">optional</span></span>
          <textarea
            className="field-input"
            rows={4}
            value={identity}
            placeholder="Who is this agent? Its role, tone, and how it should behave. (becomes IDENTITY.md)"
            onChange={(e) => setIdentity(e.target.value)}
          />
        </label>

        <div className="field">
          <span className="field-label">App UI <span className="field-opt">optional</span></span>
          <div className="seg">
            <button
              type="button"
              className={appMode === '' ? 'on' : ''}
              title="no app UI — the agent lives in this client's chat"
              onClick={() => setAppMode('')}
            >
              Chat only
            </button>
            <button
              type="button"
              className={appMode === 'browser' ? 'on' : ''}
              title="ships its own UI (agents/<id>/ui/), opened as a browser tab"
              onClick={() => setAppMode('browser')}
            >
              Browser app
            </button>
            <button
              type="button"
              className={appMode === 'window' ? 'on' : ''}
              title="ships its own UI (agents/<id>/ui/), opened in its own window like a program"
              onClick={() => setAppMode('window')}
            >
              Window app
            </button>
          </div>
        </div>

        {error && <div className="field-error">{error}</div>}

        <div className="modal-actions">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!name.trim() || busy}>
            {busy ? 'Creating…' : 'Create agent'}
          </button>
        </div>
      </form>
    </div>
  )
}

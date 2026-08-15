/* Settings, over the conversation rather than instead of it.
 *
 * A WRAPPER, deliberately: every rule about what settings mean — the two layers, the override,
 * what a patch contains, which keys can be read back — lives in SettingsView and the hooks under
 * it, untouched. This file owns nothing but the window it sits in.
 */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useRef } from 'react'
import { SettingsView } from './SettingsView'

export function SettingsModal({
  client,
  onClose,
}: {
  client: AgentdClient
  onClose: () => void
}) {
  const cardRef = useRef<HTMLDivElement>(null)

  // Escape closes. A modal you can only leave by finding the right pixel is a modal people stop
  // opening — and the close button is at the far corner from where the cursor already is.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Focus moves INTO the dialog on open, so Escape and the scroll wheel act on it rather than on
  // the thread underneath.
  useEffect(() => {
    cardRef.current?.focus()
  }, [])

  return (
    <div
      className="modal-back"
      // Only a click that both started and ended on the backdrop closes. Testing the target alone
      // dismisses the dialog when a drag that began inside it — selecting the text of an API key —
      // happens to release outside.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label="Settings" tabIndex={-1} ref={cardRef}>
        <header className="modal-head">
          <div className="modal-title">
            <span className="tile sm">⚙</span>
            <div>
              <h1>Settings</h1>
              <p>This agent, the daemon it runs on, and the keys that pay for it.</p>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </header>

        <div className="modal-body">
          <SettingsView client={client} />
        </div>
      </div>
    </div>
  )
}

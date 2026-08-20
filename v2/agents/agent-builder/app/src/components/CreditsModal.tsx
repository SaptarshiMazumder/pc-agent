/* Credits & billing — its own page, over the conversation rather than instead of it.
 *
 * A PAGE AND NOT A SETTINGS SECTION. Topping up is what a user comes looking for: they hit "out
 * of credits", and the fix has to be one click from where they read that. Buried three scrolls
 * into a config screen it is a fix nobody finds — and settings is where you go to change how the
 * thing works, not to buy something. agentd draws the same distinction (`SubscriptionView` is a
 * page off the profile menu, not a block in Settings), and this window follows it.
 *
 * THE PANEL ITSELF IS NOT WRITTEN HERE. `mountCreditsPanel` ships in the SDK, over
 * `@agentd/billing` — the same client the agentd desktop app buys through and the same one every
 * agent this builder produces will vendor. One shop, one set of rules about money, byte-identical
 * everywhere. A React re-implementation would be a second store: a second set of idempotency
 * keys, refusal messages and "has the money actually arrived yet", in an app that takes real
 * money. Agent Builder is held to the rule it enforces on its own output.
 *
 * A modal, like Settings, because this window has no router — see SettingsModal for why that is
 * the honest shape here.
 */

import { mountCreditsPanel } from '@agentd/client'
import { useEffect, useRef, useState } from 'react'

export function CreditsModal({ onClose }: { onClose: () => void }) {
  const cardRef = useRef<HTMLDivElement>(null)
  const host = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'loading' | 'shown' | 'empty'>('loading')

  // Escape closes. A dialog you can only leave by finding the right pixel is one people stop
  // opening — and the close button is at the far corner from where the cursor already is.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    cardRef.current?.focus()
  }, [])

  useEffect(() => {
    const el = host.current
    if (!el) return
    let panel: { destroy(): void } | null = null
    let live = true

    void mountCreditsPanel({ mount: el })
      .then((p) => {
        // StrictMode mounts, unmounts and remounts in development. Without this the first panel
        // is orphaned in a detached node and its balance listener is never unsubscribed.
        if (!live) {
          p.destroy()
          return
        }
        panel = p
        setState(p.shown ? 'shown' : 'empty')
      })
      .catch((e) => {
        console.error('credits panel failed', e)
        setState('empty')
      })

    return () => {
      live = false
      panel?.destroy()
    }
  }, [])

  return (
    <div
      className="modal-back"
      // Only a click that both started and ended on the backdrop closes, so a drag that began
      // inside the dialog and released outside does not dismiss it.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Credits and billing"
        tabIndex={-1}
        ref={cardRef}
      >
        <header className="modal-head">
          <div className="modal-title">
            <span className="tile sm">◈</span>
            <div>
              <h1>Credits &amp; billing</h1>
              <p>Credits pay for model calls on your account. Buy more at any time.</p>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </header>

        <div className="modal-body">
          <div className="settings-scroll">
            <div className="settings-inner">
              <div ref={host} />
              {state === 'loading' && <div className="loading">loading credits…</div>}
              {/* No accounts service, or nobody signed in. Say which, rather than leaving the
                  dialog blank and letting it read as a page that failed to load. */}
              {state === 'empty' && (
                <div className="loading">
                  Sign in to see your balance and buy credits. On a build with no accounts
                  service there is nothing to bill.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

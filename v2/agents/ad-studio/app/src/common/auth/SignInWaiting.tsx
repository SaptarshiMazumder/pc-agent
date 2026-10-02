/* The frames between: the gate checking, a code being redeemed, a sign-out under way.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. If you need something it does not expose, add it there so every agent gets it.
 *
 * ONE SCREEN FOR EVERY WAIT, centred on the page: the product's mark in a tile with a slow ring
 * around it, the product's name, a thin bar that keeps moving, and one line saying what is
 * happening. Not a card with a form missing from it — that read as a broken login. Not a bare
 * spinner either, which reads as a broken page. The mark is the agent's own when it passes one
 * (comfy passes its penguin); without one the tile holds a dot in the accent, which is still a
 * deliberate shape rather than an empty box.
 *
 * `role="status"` so a screen reader announces the note when it changes, and every animation
 * stops under `prefers-reduced-motion` (auth.css).
 */

import type { ReactNode } from 'react'

import './auth.css'

export function SignInWaiting({
  product,
  note,
  mark,
}: {
  product: string
  /** One line under the name: "Signing you in…", "Signing out…". Empty for the plain probe. */
  note: string
  /** The agent's mark, drawn inside the tile. Optional — see the header. */
  mark?: ReactNode
}) {
  return (
    <div className="signin-wrap">
      <div className="signin-wait" role="status" aria-live="polite">
        <div className="signin-wait-mark" aria-hidden="true">
          {mark ?? <span className="signin-wait-dot" />}
        </div>
        <div className="signin-wait-name">{product || 'Signing in'}</div>
        <div className="signin-wait-bar" aria-hidden="true">
          <span />
        </div>
        {note && <div className="signin-wait-note">{note}</div>}
      </div>
    </div>
  )
}

/* The one fact the gate needs from the account menu: a sign-out is under way.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. If you need something it does not expose, add it there so every agent gets it.
 *
 * WHY A MODULE AND NOT A PROP. `<Gate>` wraps the app in main.tsx; the Sign out button is three
 * components deep inside the app. Between the click and the page starting over (identity.ts
 * reloads the window once the sign-out resolves) the app used to keep rendering — signed out, a
 * "○" avatar, "Local" in the menu, a conversation list emptying — for a second or two, and that
 * was what a sign-out LOOKED like. The gate can cover those frames, but only if it hears about
 * the click, and the click happens where the gate cannot reach with a prop.
 *
 * NOTHING ELSE LIVES HERE. Signing in has no such window: the card that starts it is already the
 * only thing on screen, and the page starts over the moment it succeeds.
 */

type Listener = (signingOut: boolean) => void

let signingOut = false
const listeners = new Set<Listener>()

function tell(): void {
  for (const cb of [...listeners]) {
    try {
      cb(signingOut)
    } catch {
      /* a listener must never break a sign-out */
    }
  }
}

export const signOutTransition = {
  /** The Sign out button was pressed and the sign-out call is in flight. */
  begin(): void {
    if (signingOut) return
    signingOut = true
    tell()
  },
  /** The sign-out FAILED and the app is coming back. A success never calls this — the page
   *  starts over instead, and a flag on a page that is gone needs no clearing. */
  end(): void {
    if (!signingOut) return
    signingOut = false
    tell()
  },
  current(): boolean {
    return signingOut
  },
  /** Hear every change. Returns the unsubscribe. */
  subscribe(cb: Listener): () => void {
    listeners.add(cb)
    return () => {
      listeners.delete(cb)
    }
  },
}

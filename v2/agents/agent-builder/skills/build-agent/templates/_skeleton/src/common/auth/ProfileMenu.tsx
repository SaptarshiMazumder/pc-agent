/* ProfileMenu — the account control, and the way to reach Credits.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source.
 *
 *     const account = useAuth(client)
 *     <ProfileMenu {...account} onCredits={() => setView('credits')} />
 *
 * WHERE IT GOES: the bottom of your sidebar, where agentd puts it. Identity is ambient — you check
 * it when something surprises you (a run that says "out of credits", a publish attributed to the
 * wrong account) and otherwise never. It does not belong in a header, and it does NOT belong three
 * scrolls into a settings page: signing in is the first thing a new user needs and settings is the
 * last place they look.
 *
 * IT OFFERS NO SIGN-IN BUTTON when the build has no accounts service. The gate would render
 * nothing, which from the outside is a button that does nothing — so the reason is stated where
 * the button would have been.
 */

import { useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { Auth } from './useAuth'

export function ProfileMenu({
  auth,
  busy,
  error,
  signIn,
  signOut,
  onCredits,
}: Pick<Auth, 'auth' | 'busy' | 'error' | 'signIn' | 'signOut'> & {
  /** Show the Credits page. Omit to leave the entry out. */
  onCredits?: () => void
}) {
  const [open, setOpen] = useState(false)
  const signedIn = !!auth?.signedIn
  const canSignIn = !!auth?.available

  /* WHERE THE MENU GOES, measured rather than declared.
   *
   * IT IS PORTALLED TO <body> AND POSITIONED FIXED. It used to be `position: absolute` inside
   * this component, which put it inside the sidebar — and a sidebar has `overflow: hidden`,
   * because its lists scroll. So the menu was clipped at the rail's edge: the email and the mode
   * line were cut off mid-word, and the whole thing looked like a rendering bug.
   *
   * No ancestor can clip a portalled child, which is the same reason the hover tooltip in these
   * windows is portalled too. The cost is that CSS can no longer place it, so it is measured from
   * the button and clamped to the viewport.
   */
  const btnRef = useRef<HTMLButtonElement>(null)
  const [at, setAt] = useState<{ left: number; bottom: number } | null>(null)

  useLayoutEffect(() => {
    if (!open) return setAt(null)
    const el = btnRef.current
    if (!el) return
    const place = () => {
      const r = el.getBoundingClientRect()
      // ABOVE the button, because it sits at the bottom of the rail. Clamped so a narrow window
      // cannot push it off-screen — an 8px margin, and never past the right edge.
      const width = 248
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
      setAt({ left, bottom: Math.max(8, window.innerHeight - r.top + 8) })
    }
    place()
    // Follow the button if the window changes shape underneath an open menu.
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open])

  return (
    <div className="agentd-pm">
      {/* A backdrop, not a document listener: it closes on any outside click without this menu
          having to reason about which clicks belong to it. */}
      {open && <div className="agentd-pm-back" onClick={() => setOpen(false)} />}
      <button
        ref={btnRef}
        className={`agentd-pm-btn ${signedIn ? 'on' : ''}`}
        type="button"
        title={signedIn ? `Signed in as ${auth?.email || 'your account'}` : 'Account'}
        onClick={() => setOpen((v) => !v)}
      >
        {signedIn ? (auth?.email || 'Account').slice(0, 1).toUpperCase() : '○'}
      </button>

      {open &&
        at &&
        createPortal(
          <div className="agentd-pm-menu" style={{ left: at.left, bottom: at.bottom }}>
          <div className="agentd-pm-head">
            <div className="agentd-pm-name">{signedIn ? auth?.email || 'Signed in' : 'Local'}</div>
            <div className="agentd-pm-desc">
              {signedIn
                ? 'Cloud · platform keys, metered'
                : canSignIn
                  ? 'Your own keys (BYOK) — sign in to use Cloud'
                  : 'This build has no accounts service, so there is nobody to sign in as.'}
            </div>
            {error && <div className="agentd-pm-err">{error}</div>}
          </div>

          {onCredits && (
            <button
              className="agentd-pm-item"
              type="button"
              onClick={() => {
                onCredits()
                setOpen(false)
              }}
            >
              Credits &amp; billing
            </button>
          )}

          {canSignIn && (
            <button
              className="agentd-pm-item"
              type="button"
              disabled={busy}
              onClick={() => {
                // signOut is a round trip; signIn only raises the card. Close either way.
                if (signedIn) void signOut()
                else signIn()
                setOpen(false)
              }}
            >
              {busy ? 'Working…' : signedIn ? 'Sign out' : 'Sign in'}
            </button>
          )}
          </div>,
          document.body,
        )}
    </div>
  )
}

/** Minimal styling, so the menu is usable in an app that has not styled it yet. Every value is a
 *  custom property with a fallback — set the properties, do not fork this. */
export const PROFILE_MENU_CSS = `
.agentd-pm{position:relative;display:flex}
.agentd-pm-back{position:fixed;inset:0;z-index:40}
.agentd-pm-btn{width:36px;height:36px;border-radius:50%;cursor:pointer;
  border:1px solid var(--pm-border,rgba(255,255,255,.16));background:transparent;
  color:var(--pm-fg,#e8eaed);font:inherit}
.agentd-pm-btn.on{border-color:var(--pm-accent,#8ab4f8)}
.agentd-pm-menu{position:absolute;bottom:calc(100% + 8px);left:0;z-index:41;min-width:214px;
  padding:6px;border-radius:12px;background:var(--pm-card,#14171d);
  border:1px solid var(--pm-border,rgba(255,255,255,.16));color:var(--pm-fg,#e8eaed)}
.agentd-pm-head{padding:6px 9px 8px;max-width:220px}
.agentd-pm-name{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.agentd-pm-desc{font-size:11px;color:var(--pm-muted,#9aa0a6);margin-top:2px}
.agentd-pm-err{font-size:11px;color:var(--pm-bad,#f5a3a8);margin-top:6px;line-height:1.4}
.agentd-pm-item{display:flex;align-items:center;gap:10px;width:100%;text-align:left;
  padding:9px 10px;border:0;border-radius:8px;cursor:pointer;background:transparent;
  color:inherit;font:inherit;font-size:13px}
.agentd-pm-item:hover:not(:disabled){background:var(--pm-hover,rgba(255,255,255,.06))}
.agentd-pm-item:disabled{opacity:.6;cursor:default}
`

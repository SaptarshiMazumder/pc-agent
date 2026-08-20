/**
 * THE ACCOUNT FOOTER — who is signed in, what is left, and the way out.
 *
 * The shell keeps this at the bottom of its sidebar (ProfileMenu + SettingsMenu), and an agent
 * window that omits it is not a smaller product, it is a broken one: the user cannot see which
 * account they are spending, cannot read a balance until a run fails at 402, and cannot sign out
 * at all — on a shared machine that last one is the whole problem.
 *
 * The gear opens the full settings page (settings.tsx): account, the models in use, tools and
 * plugins. An earlier version of this comment claimed those were impossible for an app — they are
 * not, and the gateway says so directly: `config.get` and `plugins.catalog` are both in
 * APP_SCOPED_METHODS, the first one added precisely so "an agent app renders its own settings
 * page". What genuinely stays out is the marketplace, projects and admin, which are host-only.
 *
 * THE HOST SUPPLIES THE MECHANISM, not this file: identity and balance come from the accounts
 * service over plain HTTP with the window's own token, and the theme is local to the page.
 *
 * `credits()` and `signOut()` are handed in by the app (which owns an SDK and therefore a
 * TokenManager); this only renders. Same seam rule as the rest of the bundle — no credential
 * handling here, ever.
 */

import { useCallback, useEffect, useState, type JSX } from 'react'
import { LogOut, Moon, Settings, Sun, User } from 'lucide-react'

export interface AccountAdapter {
  /** The signed-in email, for the footer's label. '' renders as the account being unknown. */
  email?: string
  /** Credits left, or null where this deployment does not meter. Polled after each run. */
  credits?(): Promise<number | null>
  /** Sign out: revoke, drop the session, and let the window re-gate. */
  signOut?(): Promise<void>
  /**
   * The credit packs on sale, plus WHICH RAIL is behind them.
   *
   * `provider` and `note` are the payment gateway's own words for what a purchase will do, and
   * they are DISPLAYED, never branched on — the accounts service is explicit that no code path
   * may work only because payments happen to be mocked. So a deployment on the null rail says so
   * in the page instead of quietly pretending money moved.
   */
  products?(): Promise<{ products: CreditPack[]; provider: string; note: string }>
  /** Buy one pack. Resolves with the new balance. */
  buy?(productId: string): Promise<{ credits: number; creditsRemaining: number; detail: string }>
}

export interface CreditPack {
  id: string
  /** `title` is what the products table calls it; `name` is accepted for an older catalogue. */
  title?: string
  name?: string
  credits?: number
  price_usd?: number
  description?: string
}

type Theme = 'light' | 'dark'
const THEME_KEY = 'agentd-theme' // the SHELL's key, so a window and the app agree on the theme

function initialTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* storage unavailable — the theme just will not persist */
  }
}

/**
 * @param runsCompleted bump it when a run ends; the balance re-reads. A poll would be either too
 *        slow to be believed or too chatty to be polite, and the only moment the number can have
 *        changed is when work finished.
 */
export function AccountFooter({
  account,
  runsCompleted,
  onSettings,
  settingsOpen
}: {
  account?: AccountAdapter
  runsCompleted: number
  onSettings?(): void
  settingsOpen?: boolean
}): JSX.Element | null {
  const [credits, setCredits] = useState<number | null>(null)
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const [busy, setBusy] = useState(false)

  useEffect(() => applyTheme(theme), [theme])

  const read = useCallback(async () => {
    if (!account?.credits) return
    try {
      setCredits(await account.credits())
    } catch {
      setCredits(null) // a deployment that does not meter, or a balance we could not reach
    }
  }, [account])

  useEffect(() => {
    void read()
  }, [read, runsCompleted])

  if (!account) return null

  return (
    <div className="app-account">
      <div className="app-account-who" title={account.email || 'Signed in'}>
        <span className="app-account-mark" aria-hidden="true"><User size={15} /></span>
        <span className="app-account-mail">{account.email || 'Signed in'}</span>
      </div>
      {credits !== null && (
        <div
          className={`app-account-credits ${credits === 0 ? 'empty' : ''}`}
          title={
            credits === 0
              ? 'Out of credits — the next message will be refused.'
              : 'Platform credits left on this account. Updates after each run.'
          }
        >
          {credits === 0 ? 'no credits left' : `${credits.toLocaleString()} credits`}
        </div>
      )}
      <div className="app-account-actions">
        {onSettings && (
          <button
            className={`cv-btn ${settingsOpen ? 'on' : ''}`}
            title="Settings — account, models, tools and plugins"
            onClick={onSettings}
          >
            <Settings size={15} />
          </button>
        )}
        <button
          className="cv-btn"
          title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
          onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
        {account.signOut && (
          <button
            className="cv-btn"
            title="Sign out"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await account.signOut?.()
              } finally {
                setBusy(false)
              }
            }}
          >
            <LogOut size={15} />
          </button>
        )}
      </div>
    </div>
  )
}

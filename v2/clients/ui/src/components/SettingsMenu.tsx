import { useEffect, useState, type ReactNode } from 'react'
import { Settings, SlidersHorizontal, Database, User, CreditCard, ShieldCheck } from 'lucide-react'

import { isAdmin } from '../lib/admin'
import { useAuthSession } from '../lib/auth'
import { useBilling } from '../lib/billing'
import { useApp, type View } from '../state/store'

/**
 * The sidebar's "gear" menu: replaces the old single Settings button with a popover of
 * app-level destinations (Settings, Data sources, Account, Credits). Used in both the
 * full footer and the collapsed rail (variant switches the trigger styling + popover anchor).
 */
const ITEMS: { id: View; label: string; icon: ReactNode }[] = [
  { id: 'settings', label: 'Settings', icon: <SlidersHorizontal size={16} /> },
  { id: 'datasources', label: 'Data sources', icon: <Database size={16} /> },
  { id: 'account', label: 'Account', icon: <User size={16} /> },
  // Same destination either way; the word changes because the page does. On a metered account it
  // is where you top up, and "Subscription" is where nobody would look for that.
  { id: 'subscription', label: 'Subscription', icon: <CreditCard size={16} /> }
]

/** The control plane, appended for admins only. Its own entry rather than a row inside Settings:
 *  it governs the whole platform, not this install, and burying it under Settings would imply the
 *  opposite. */
const ADMIN_ITEM: { id: View; label: string; icon: ReactNode } = {
  id: 'admin',
  label: 'Admin',
  icon: <ShieldCheck size={16} />
}

export default function SettingsMenu({ variant }: { variant: 'footer' | 'rail' }) {
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const { billing } = useBilling()
  const session = useAuthSession()
  const [open, setOpen] = useState(false)
  const [admin, setAdmin] = useState(false)

  // Asked once per account, not per render. Not a security boundary — the server refuses every
  // /admin/* call regardless; this only decides whether the entry is drawn.
  useEffect(() => {
    let live = true
    if (!session) {
      setAdmin(false)
      return
    }
    void isAdmin(session.accountId).then((yes) => live && setAdmin(yes))
    return () => {
      live = false
    }
  }, [session?.accountId])

  const items = admin ? [...ITEMS, ADMIN_ITEM] : ITEMS
  const active = items.some((i) => i.id === view)
  const label = (it: { id: View; label: string }): string =>
    it.id === 'subscription' && billing ? 'Credits & billing' : it.label

  return (
    <div className="menu-wrap">
      {open && <div className="menu-backdrop" onClick={() => setOpen(false)} />}
      {variant === 'footer' ? (
        <button
          className={`icon-btn footer-icon ${active ? 'menu-trigger-on' : ''}`}
          title="Settings & more"
          onClick={() => setOpen((v) => !v)}
        >
          <Settings size={17} />
        </button>
      ) : (
        <button className={`rail-btn ${active ? 'active' : ''}`} title="Settings & more" onClick={() => setOpen((v) => !v)}>
          <Settings size={18} />
        </button>
      )}
      {open && (
        <div className={`app-menu ${variant === 'rail' ? 'app-menu--rail' : ''}`}>
          <div className="app-menu-label">Menu</div>
          {items.map((it) => (
            <button
              key={it.id}
              className={`app-menu-item ${view === it.id ? 'active' : ''}`}
              onClick={() => {
                setView(it.id)
                setOpen(false)
              }}
            >
              {it.icon}
              <span>{label(it)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

import { useState, type ReactNode } from 'react'
import { Settings, SlidersHorizontal, Database, User, CreditCard } from 'lucide-react'

import { useApp, type View } from '../state/store'

/**
 * The sidebar's "gear" menu: replaces the old single Settings button with a popover of
 * app-level destinations (Settings, Data sources, Account, Subscription). Used in both the
 * full footer and the collapsed rail (variant switches the trigger styling + popover anchor).
 */
const ITEMS: { id: View; label: string; icon: ReactNode }[] = [
  { id: 'settings', label: 'Settings', icon: <SlidersHorizontal size={16} /> },
  { id: 'datasources', label: 'Data sources', icon: <Database size={16} /> },
  { id: 'account', label: 'Account', icon: <User size={16} /> },
  { id: 'subscription', label: 'Subscription', icon: <CreditCard size={16} /> }
]

export default function SettingsMenu({ variant }: { variant: 'footer' | 'rail' }) {
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const [open, setOpen] = useState(false)
  const active = ITEMS.some((i) => i.id === view)

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
          {ITEMS.map((it) => (
            <button
              key={it.id}
              className={`app-menu-item ${view === it.id ? 'active' : ''}`}
              onClick={() => {
                setView(it.id)
                setOpen(false)
              }}
            >
              {it.icon}
              <span>{it.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

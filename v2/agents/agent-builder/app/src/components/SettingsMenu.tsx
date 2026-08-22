import { useState, type ReactNode } from 'react'
import { CreditCard, Settings, SlidersHorizontal } from 'lucide-react'

/**
 * The sidebar's "gear" menu: a popover of app-level destinations rather than a button that opens
 * one thing. Used in both the full footer and the collapsed rail (`variant` switches the trigger
 * styling and the popover's anchor).
 *
 * COPIED FROM agentd, with three of its five entries removed — *Data sources*, *Account* and
 * *Admin* are pages this window has no route to and no permission to serve. What is left is two,
 * and one of them (Credits & billing) also sits in the ProfileMenu immediately to the left. That
 * duplication is agentd's too: it carries Credits in both menus because it is what a user comes
 * looking for when a run stops, and making them guess which menu it is under is the failure this
 * is guarding against.
 */
const ITEMS: { id: 'settings' | 'credits'; label: string; icon: ReactNode }[] = [
  { id: 'settings', label: 'Settings', icon: <SlidersHorizontal size={16} /> },
  { id: 'credits', label: 'Credits & billing', icon: <CreditCard size={16} /> },
]

export function SettingsMenu({
  variant = 'footer',
  onSettings,
  onCredits,
}: {
  variant?: 'footer' | 'rail'
  onSettings: () => void
  onCredits: () => void
}) {
  const [open, setOpen] = useState(false)

  const run = (id: (typeof ITEMS)[number]['id']) => () => {
    setOpen(false)
    if (id === 'settings') onSettings()
    else onCredits()
  }

  return (
    <div className="menu-wrap">
      {/* A backdrop rather than a document listener: it closes on any outside click without this
          menu having to reason about which clicks belong to it. */}
      {open && <div className="menu-backdrop" onClick={() => setOpen(false)} />}
      {variant === 'footer' ? (
        <button
          className={`icon-btn footer-icon ${open ? 'menu-trigger-on' : ''}`}
          title="Settings & more"
          onClick={() => setOpen((v) => !v)}
        >
          <Settings size={17} />
        </button>
      ) : (
        <button
          className={`rail-btn ${open ? 'active' : ''}`}
          title="Settings & more"
          onClick={() => setOpen((v) => !v)}
        >
          <Settings size={18} />
        </button>
      )}
      {open && (
        <div className={`app-menu ${variant === 'rail' ? 'app-menu--rail' : ''}`}>
          <div className="app-menu-label">Menu</div>
          {ITEMS.map((it) => (
            <button key={it.id} className="app-menu-item" type="button" onClick={run(it.id)}>
              {it.icon}
              <span>{it.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

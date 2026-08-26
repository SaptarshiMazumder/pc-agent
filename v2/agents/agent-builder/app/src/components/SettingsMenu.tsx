import { useState, type ReactNode } from 'react'
import { Building2, CreditCard, Settings, SlidersHorizontal } from 'lucide-react'

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
const ITEMS: { id: 'settings' | 'credits' | 'orgs'; label: string; icon: ReactNode }[] = [
  { id: 'settings', label: 'Settings', icon: <SlidersHorizontal size={16} /> },
  { id: 'credits', label: 'Credits & billing', icon: <CreditCard size={16} /> },
  // SEATS ARE BOUGHT ONCE AND MET EVERYWHERE. An enterprise that buys seats meets them in the
  // assistant, here, and inside every agent this window builds — one page, copied, not three
  // ideas of what a seat is.
  { id: 'orgs', label: 'Organizations', icon: <Building2 size={16} /> },
]

export function SettingsMenu({
  variant = 'footer',
  onSettings,
  onCredits,
  onOrgs,
}: {
  variant?: 'footer' | 'rail'
  onSettings: () => void
  onCredits: () => void
  onOrgs: () => void
}) {
  const [open, setOpen] = useState(false)

  // A LOOKUP, not a chain. This was `if settings ... else credits`, so adding a third entry to
  // ITEMS above would silently have opened Credits — the menu and its handler have to be one
  // list, or the next entry is a bug rather than a line.
  const run = (id: (typeof ITEMS)[number]['id']) => () => {
    setOpen(false)
    const go = { settings: onSettings, credits: onCredits, orgs: onOrgs }
    go[id]()
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

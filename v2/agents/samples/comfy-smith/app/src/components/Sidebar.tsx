/* The sidebar: named destinations, grouped, with the account at the foot.
 *
 * A 52px strip of emoji was the previous version and it was wrong twice. Emoji are somebody
 * else's font — they render at a different weight and colour on every machine, which is most of
 * what makes a hand-built app look unfinished. And an icon with no label is a guess: "🗂" is
 * files, or folders, or archive, or export.
 *
 * The TOOLS group is not decoration either. Those three open direct tool calls — no chat turn,
 * no model, no tokens — so "what is installed on that server" is answered in milliseconds
 * instead of costing a conversation.
 */

import type { AuthState } from '../agentd'

export type View = 'chat' | 'artifacts' | 'server' | 'models' | 'nodes' | 'settings'

const MAIN: Array<{ id: View; label: string; icon: Icon }> = [
  { id: 'chat', label: 'Chat', icon: 'chat' },
  { id: 'artifacts', label: 'Workflows', icon: 'file' },
]

const TOOLS: Array<{ id: View; label: string; icon: Icon }> = [
  { id: 'server', label: 'Server', icon: 'server' },
  { id: 'models', label: 'Models', icon: 'box' },
  { id: 'nodes', label: 'Nodes', icon: 'node' },
]

export function Sidebar({
  view,
  onView,
  status,
  alert,
  auth,
  onSignIn,
  onSignOut,
}: {
  view: View
  onView: (v: View) => void
  status: string
  /** A required setting with no value — flagged on a page the user is not looking at, because
   *  by the time they notice it the agent has already failed for a reason it never explained. */
  alert?: boolean
  auth: AuthState | null
  onSignIn: () => void
  onSignOut: () => void
}) {
  return (
    <nav className="sidebar">
      <div className="brand">
        <span className="brand-mark" />
        <span className="brand-name">Comfy Smith</span>
      </div>

      <Group label="Main" items={MAIN} view={view} onView={onView} />
      <Group label="Tools" items={TOOLS} view={view} onView={onView} />

      <span className="side-grow" />

      <Group
        label=""
        items={[{ id: 'settings' as View, label: 'Settings', icon: 'gear' as Icon }]}
        view={view}
        onView={onView}
        alertOn="settings"
        alert={alert}
      />

      {auth?.signedIn ? (
        <button className="nav-item" onClick={onSignOut}>
          <Glyph name="out" />
          <span>Sign out</span>
        </button>
      ) : (
        <button className="nav-item" onClick={onSignIn}>
          <Glyph name="in" />
          <span>Sign in</span>
        </button>
      )}

      <div className="account">
        <span className="avatar-sm">{initials(auth?.email)}</span>
        <span className="account-who">
          <strong>{auth?.email ? auth.email.split('@')[0] : 'Not signed in'}</strong>
          <span className="account-sub">
            {auth?.signedIn ? auth.email : 'no account on this window'}
          </span>
        </span>
        {/* The connection dot lives here rather than floating: when the daemon goes away a page
            that merely stops responding is unexplainable, and this is the explanation. */}
        <span className={`dot ${status}`} title={`daemon: ${status}`} />
      </div>
    </nav>
  )
}

function Group({
  label,
  items,
  view,
  onView,
  alertOn,
  alert,
}: {
  label: string
  items: Array<{ id: View; label: string; icon: Icon }>
  view: View
  onView: (v: View) => void
  alertOn?: View
  alert?: boolean
}) {
  return (
    <div className="nav-group">
      {label && <span className="nav-label">{label}</span>}
      {items.map((item) => (
        <button
          key={item.id}
          className={`nav-item ${view === item.id ? 'on' : ''}`}
          onClick={() => onView(item.id)}
        >
          <Glyph name={item.icon} />
          <span>{item.label}</span>
          {alertOn === item.id && alert && <span className="nav-dot" />}
        </button>
      ))}
    </div>
  )
}

type Icon = 'chat' | 'file' | 'server' | 'box' | 'node' | 'gear' | 'in' | 'out'

/** Inline SVG on a 16px grid, stroked with `currentColor` so every icon inherits the state of
 *  the row it is in — hover, active, disabled — with no per-icon colour anywhere. */
function Glyph({ name }: { name: Icon }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  switch (name) {
    case 'chat':
      return (
        <svg {...common}>
          <path d="M14 9.5a2 2 0 0 1-2 2H6l-3.5 2.5v-2.5H4a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2Z" />
        </svg>
      )
    case 'file':
      return (
        <svg {...common}>
          <path d="M9 1.5H4.5a1.5 1.5 0 0 0-1.5 1.5v10a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5V5.5Z" />
          <path d="M9 1.5v4h4" />
        </svg>
      )
    case 'server':
      return (
        <svg {...common}>
          <rect x="2" y="2.5" width="12" height="4.5" rx="1.2" />
          <rect x="2" y="9" width="12" height="4.5" rx="1.2" />
          <path d="M4.5 4.75h.01M4.5 11.25h.01" />
        </svg>
      )
    case 'box':
      return (
        <svg {...common}>
          <path d="M8 1.8 14 5v6l-6 3.2L2 11V5Z" />
          <path d="M2 5l6 3.2L14 5M8 8.2v6" />
        </svg>
      )
    case 'node':
      return (
        <svg {...common}>
          <circle cx="4" cy="4" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="12" cy="4" r="2" />
          <path d="M6 4h4M4 6v4a2 2 0 0 0 2 2h4" />
        </svg>
      )
    case 'gear':
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="2.2" />
          <path d="M8 1.6v1.8M8 12.6v1.8M14.4 8h-1.8M3.4 8H1.6M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3M12.5 12.5l-1.3-1.3M4.8 4.8 3.5 3.5" />
        </svg>
      )
    case 'in':
      return (
        <svg {...common}>
          <path d="M10 14h2.5a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 12.5 2H10" />
          <path d="M5.5 11 2 8l3.5-3M2 8h8" />
        </svg>
      )
    case 'out':
      return (
        <svg {...common}>
          <path d="M6 14H3.5A1.5 1.5 0 0 1 2 12.5v-9A1.5 1.5 0 0 1 3.5 2H6" />
          <path d="M10.5 11 14 8l-3.5-3M14 8H6" />
        </svg>
      )
  }
}

function initials(email?: string): string {
  if (!email) return '–'
  const name = email.split('@')[0] || ''
  const parts = name.split(/[._-]/).filter(Boolean)
  return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || name.slice(0, 2).toUpperCase()
}

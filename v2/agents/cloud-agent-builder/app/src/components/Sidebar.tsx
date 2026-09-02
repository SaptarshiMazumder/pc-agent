/* The sidebar — agentd's, copied, with the rows this window actually has.
 *
 * WHY IT IS A COPY. A user moves between the desktop app and this window constantly while
 * building, and two sidebars that arrange the same facts differently make that a re-orientation
 * every time. So this is agentd's `Sidebar.tsx` rather than something shaped like it: same
 * structure, same class names, same store reads, same hover behaviour. Where the two differ, the
 * difference is deliberate and noted here.
 *
 * WHAT IS DELIBERATELY ABSENT:
 *
 *   Projects        `projects.list` is host-only; the daemon refuses it on an app connection. An
 *                   omitted row is honest, a row that answers "method not allowed" is not.
 *   Samples         agentd shows the reference agents it ships in their own collapsed section.
 *                   This window filters them out of the roster entirely (see `openable`).
 *   Theme toggle    live in agentd; here it holds its position and stays disabled — this window
 *                   is pinned dark until the last hardcoded overlays in styles.css are tokenised.
 *
 * TWO ENTRY POINTS, NAMED AFTER THE WORK, where agentd has one "New chat" — which is what this
 * agent does rather than what it is FOR. It was a single row once, so every conversation opened on
 * an empty composer and began by guessing whether this was a new agent or an existing one. The
 * guess is now a question, asked once, before the chat (see StartModal).
 */

import {
  ChevronDown,
  ChevronRight,
  LayoutGrid,
  PanelLeft,
  Pencil,
  Plus,
  Search,
  SquarePen,
  Sun,
  Users,
  History,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

import type { AgentdClient, AuthState } from '@agentd/client'
import logo from '../assets/brick.svg'
import { agentColor, agentInitials } from '../lib/agentPresentation'
import { openable } from '../agentd/roster'
import { useApp, useSubject } from '../state/store'
import { ProfileMenu } from './ProfileMenu'
import RunModeBadge from '../../../../agent-builder/skills/build-agent/templates/_common/runmode/RunModeBadge'
import SearchBox from './SearchBox'
import { SettingsMenu } from './SettingsMenu'
import SessionItem from './SessionItem'

/** A collapsible section header (Agents / Recents). Clicking the label toggles open/closed;
 *  the caret + optional "+" action appear on hover. */
function SectionHead({
  icon,
  label,
  open,
  onToggle,
  onAdd,
  addTitle,
  extraClass,
}: {
  icon: ReactNode
  label: string
  open: boolean
  onToggle: () => void
  onAdd?: () => void
  addTitle?: string
  extraClass?: string
}) {
  return (
    <div
      className={`section-label section-head ${extraClass ?? ''}`}
      onClick={onToggle}
      title={`${open ? 'collapse' : 'expand'} ${label.toLowerCase()}`}
    >
      {icon}
      <span className="section-title">{label}</span>
      {onAdd && (
        <button
          className="section-add"
          title={addTitle}
          onClick={(e) => {
            e.stopPropagation()
            onAdd()
          }}
        >
          <Plus size={14} />
        </button>
      )}
      <span className="section-caret">
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </span>
    </div>
  )
}

/** A compact ChatGPT-style nav row (icon + label). */
function NavRow({
  icon,
  label,
  active,
  onClick,
  title,
}: {
  icon: ReactNode
  label: string
  active?: boolean
  onClick: () => void
  title?: string
}) {
  return (
    <button
      className={`nav-row ${active ? 'active' : ''}`}
      onClick={onClick}
      title={title || label}
    >
      {icon}
      <span className="nav-row-label">{label}</span>
    </button>
  )
}

export function Sidebar({
  openKey,
  onOpenChat,
  onPickAgent,
  onCreate,
  onEdit,
  onSettings,
  onCredits,
  onOrgs,
  auth,
  authError,
  onSignIn,
  onSignOut,
  client,
  status,
  daemonVersion,
}: {
  /** Which conversation is open. */
  openKey: string
  onOpenChat: (key: string) => void
  onPickAgent: (id: string) => void
  onCreate: () => void
  onEdit: () => void
  onSettings: () => void
  onCredits: () => void
  onOrgs: () => void
  auth: AuthState | null
  authError: string
  onSignIn: () => Promise<void> | void
  onSignOut: () => Promise<void> | void
  /** The daemon connection — the run-mode badge reads/sets the mode through it. */
  client?: AgentdClient
  status: string
  daemonVersion: string
}) {
  const agents = useApp((s) => s.agents)
  const viewOrg = useApp((s) => s.viewOrg)
  const chats = useApp((s) => s.chats)
  const selected = useSubject()
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const collapsed = useApp((s) => s.sidebarCollapsed)
  const toggleSidebar = useApp((s) => s.toggleSidebar)

  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [sectionOpen, setSectionOpen] = useState({ mine: true, recents: true })
  const toggleSection = (k: keyof typeof sectionOpen): void =>
    setSectionOpen((s) => ({ ...s, [k]: !s[k] }))

  const q = query.trim().toLowerCase()
  // BOTH LISTS, from one box. agentd filters only its chats, because its agents are a short fixed
  // roster and its history is the long thing. Here the roster is the long thing too — this window
  // is where every agent on the machine comes from — so a search that reached only one of them
  // would be a search you have to know the shape of before you use it.
  const shown = openable(agents).filter(
    (a) => !q || `${a.name || ''} ${a.id} ${a.tagline || ''}`.toLowerCase().includes(q),
  )
  // The account's own layer vs everything else — same rule as agentd's sidebar, and for the
  // same reason: `mine` is presumed true for the whole shared catalogue, so only the LAYER can
  // say which agents are actually this account's. Empty when signed out, so the section hides.
  const mineShown = shown.filter((a) => a.layer === 'account')
  const shownChats = chats.filter(
    (c) => !q || `${c.title || ''} ${c.snippet || ''}`.toLowerCase().includes(q),
  )

  // ---- collapsed icon rail --------------------------------------------------
  if (collapsed) {
    return (
      <aside className="rail sidebar sidebar--rail">
        <img className="brand-logo brand-logo--rail" src={logo} alt="" />
        <button className="rail-btn" title="expand sidebar" onClick={toggleSidebar}>
          <PanelLeft size={17} />
        </button>
        <button className="rail-primary" title="New agent" onClick={onCreate}>
          <SquarePen size={17} />
        </button>
        <button className="rail-btn" title="Edit an agent" onClick={onEdit}>
          <Pencil size={17} />
        </button>
        <button className="rail-btn" title="search" onClick={toggleSidebar}>
          <Search size={17} />
        </button>
        <div className="rail-sep" />
        <div className="rail-agents">
          {openable(agents).map((a) => (
            <button
              key={a.id}
              className={`rail-agent ${a.id === selected?.id ? 'active' : ''}`}
              title={a.name || a.id}
              onClick={() => onPickAgent(a.id)}
            >
              <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>
                {agentInitials(a.name, a.id)}
              </span>
            </button>
          ))}
        </div>
        <div className="rail-spacer" />
        <ProfileMenu
          onOrgs={() => viewOrg('')}
          onOrg={viewOrg}
          variant="rail"
          auth={auth}
          error={authError}
          onCredits={onCredits}
          onSignIn={onSignIn}
          onSignOut={onSignOut}
        />
        <button
          className={`rail-btn ${view === 'myagents' ? 'active' : ''}`}
          title="Agents"
          onClick={() => setView('myagents')}
        >
          <LayoutGrid size={18} />
        </button>
        <SettingsMenu variant="rail" onSettings={onSettings} onCredits={onCredits} onOrgs={onOrgs} />
        <button className="rail-btn" title="Light mode — not available yet" disabled>
          <Sun size={17} />
        </button>
      </aside>
    )
  }

  // ---- full sidebar ---------------------------------------------------------
  return (
    <aside className="rail sidebar">
      <div className="brand">
        <img className="brand-logo" src={logo} alt="" />
        <span className="brand-name">Cloud Agent Builder</span>
        {/* Connection state, in the one place a user already looks for it. When the daemon goes
            away a window that merely stops responding is unexplainable; this is the explanation.
            agentd only ever prints "live" and fades the dot — this window says which of the three
            states it is in, because it is the one you use while restarting the daemon. */}
        <span
          className="live"
          title={`${status}${daemonVersion ? ` · agentd ${daemonVersion}` : ''}`}
        >
          <span className="live-dot" style={{ opacity: status === 'open' ? 1 : 0.3 }} />
          {status === 'open' ? 'live' : status === 'closed' ? 'down' : '…'}
        </span>
        <button className="icon-btn icon-btn--sm" title="collapse sidebar" onClick={toggleSidebar}>
          <PanelLeft size={17} />
        </button>
      </div>

      <div className="nav-rows">
        <NavRow
          icon={<SquarePen size={17} />}
          label="New agent"
          onClick={onCreate}
          title="Create a new agent"
        />
        <NavRow
          icon={<Pencil size={17} />}
          label="Edit an agent"
          onClick={onEdit}
          title="Work on an agent you already have"
        />
        {searchOpen ? (
          <SearchBox
            className="search nav-search"
            value={query}
            onChange={setQuery}
            placeholder="Search agents & chats"
            iconSize={16}
            autoFocus
            onBlur={() => {
              if (!query.trim()) setSearchOpen(false)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setQuery('')
                setSearchOpen(false)
              }
            }}
          />
        ) : (
          <NavRow
            icon={<Search size={17} />}
            label="Search"
            onClick={() => setSearchOpen(true)}
            title="Search agents & chats"
          />
        )}
      </div>

      {/* MY AGENTS — the signed-in account's own layer. Above the catalogue because these are
          the ones being built here, and absent when signed out rather than empty. */}
      {mineShown.length > 0 && (
        <>
          <SectionHead
            icon={<Users size={14} />}
            label="My agents"
            open={sectionOpen.mine}
            onToggle={() => toggleSection('mine')}
          />
          {sectionOpen.mine && (
            <div className="agents-list">
              {mineShown.map((a) => (
                <button
                  key={a.id}
                  className={`row ${a.id === selected?.id ? 'active' : ''}`}
                  title={a.description || a.tagline || a.id}
                  onClick={() => onPickAgent(a.id)}
                >
                  <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>
                    {agentInitials(a.name, a.id)}
                  </span>
                  <span className="row-main">
                    <span className="row-title">{a.name || a.id}</span>
                    <span className="row-sub">{a.tagline || a.id}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {/* NO INLINE "AGENTS" LISTING. The platform's agents were enumerated here; the full list
          now lives on the My Agents shelf (the rail button above, /agents), so this panel keeps
          the account's OWN agents and its chats rather than duplicating the catalogue.
          Creating an agent is unaffected — the "+" that sat on this section head is also the
          rail's "New agent" button and the empty-state button above. */}

      {/* RECENTS */}
      <div className="sidebar-scroll">
        <SectionHead
          icon={<History size={14} />}
          label="Recents"
          open={sectionOpen.recents}
          onToggle={() => toggleSection('recents')}
          extraClass="section-chats"
        />
        {sectionOpen.recents && (
          <>
            {shownChats.slice(0, 100).map((c) => (
              <SessionItem
                key={c.sessionId}
                session={c}
                active={c.sessionId === openKey}
                onOpen={() => onOpenChat(c.sessionId)}
              />
            ))}
            {shownChats.length === 0 && (
              <div className="row-sub list-empty">
                {q ? 'no chats match' : 'no conversations yet'}
              </div>
            )}
          </>
        )}
      </div>

      <div className="footer-nav">
        <ProfileMenu
          onOrgs={() => viewOrg('')}
          onOrg={viewOrg}
          auth={auth}
          error={authError}
          onCredits={onCredits}
          onSignIn={onSignIn}
          onSignOut={onSignOut}
        />
        {/* Always on screen, in every view — whose keys pay for model calls; click to switch. */}
        <RunModeBadge client={client} />
        <button
          className={`icon-btn footer-icon push-end ${view === 'myagents' ? 'active' : ''}`}
          title="Agents"
          onClick={() => setView('myagents')}
        >
          <LayoutGrid size={17} />
        </button>
        <SettingsMenu onSettings={onSettings} onCredits={onCredits} onOrgs={onOrgs} />
        {/* PLACEHOLDER, and deliberately inert — this window is pinned dark (see styles.css). The
            button holds the position agentd's theme toggle occupies so the two footers do not
            drift apart before the feature lands. */}
        <button className="icon-btn footer-icon" title="Light mode — not available yet" disabled>
          <Sun size={17} />
        </button>
      </div>
    </aside>
  )
}

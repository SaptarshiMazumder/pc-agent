import { useState, type ReactNode } from 'react'
import { Blocks, Building2, ChevronDown, ChevronRight, Folder, History, LayoutGrid, Moon, PanelLeft, Plus, Search, ShieldCheck, SquarePen, Sun, UserPlus, Users } from 'lucide-react'

import logo from '../assets/nakama.svg'
import { agentColor, agentInitials, agentTag, MAIN_AGENT_ID } from '../lib/agentPresentation'
import { openAdminConsole, useIsAdmin } from '../lib/admin'
import { useMyOrgs } from '../lib/orgs'
import { launchStandaloneApp, listableAgents, standaloneApps } from '../lib/standaloneApps'
import { useApp } from '../state/store'
import NewAgentModal from './NewAgentModal'
import ProfileMenu from './ProfileMenu'
import RunModeBadge from './RunModeBadge'
import SearchBox from './SearchBox'
import SessionItem from './SessionItem'
import SettingsMenu from './SettingsMenu'

/** A collapsible section header (Agents / Recents). Clicking the label toggles open/closed;
 *  the caret + optional "+" action appear on hover. */
function SectionHead({
  icon,
  label,
  open,
  onToggle,
  onAdd,
  addTitle,
  extraClass
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
        <button className="section-add" title={addTitle} onClick={(e) => { e.stopPropagation(); onAdd() }}>
          <Plus size={14} />
        </button>
      )}
      <span className="section-caret">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
    </div>
  )
}

/** A compact ChatGPT-style nav row (icon + label), used for New chat / Search / Projects. */
function NavRow({
  icon,
  label,
  active,
  onClick,
  title
}: {
  icon: ReactNode
  label: string
  active?: boolean
  onClick: () => void
  title?: string
}) {
  return (
    <button className={`nav-row ${active ? 'active' : ''}`} onClick={onClick} title={title || label}>
      {icon}
      <span className="nav-row-label">{label}</span>
    </button>
  )
}

export default function Sidebar() {
  const flavor = useApp((s) => s.flavor)
  const agents = useApp((s) => s.agents)
  const currentAgentId = useApp((s) => s.currentAgentId)
  const viewedAgentId = useApp((s) => s.viewedAgentId)
  const viewAgent = useApp((s) => s.viewAgent)
  const openAgentApp = useApp((s) => s.openAgentApp)
  const newChat = useApp((s) => s.newChat)
  const recents = useApp((s) => s.recents)
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const resumeSession = useApp((s) => s.resumeSession)
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const viewOrg = useApp((s) => s.viewOrg)
  const connection = useApp((s) => s.connection)
  const theme = useApp((s) => s.theme)
  const toggleTheme = useApp((s) => s.toggleTheme)
  const collapsed = useApp((s) => s.sidebarCollapsed)
  const toggleSidebar = useApp((s) => s.toggleSidebar)

  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [sectionOpen, setSectionOpen] = useState({ mine: true, samples: false, recents: true })
  const toggleSection = (k: keyof typeof sectionOpen): void =>
    setSectionOpen((s) => ({ ...s, [k]: !s[k] }))
  const [newAgent, setNewAgent] = useState(false)

  const q = query.trim().toLowerCase()
  const chats = recents.filter((s) => !q || (s.title || s.sessionId).toLowerCase().includes(q))
  // 'main' is the DEFAULT agent (what a plain New chat talks to) — hide it from the roster so
  // the list shows only the named agents you created. It's still the default everywhere else.
  // SAMPLES ARE NOT THE USER'S AGENTS. They are reference implementations we ship, runnable
  // so they cannot rot, but listing them beside the agents someone actually built is the
  // conflation the `sample` flag exists to prevent — so they get their own collapsed section.
  // MY AGENTS = the account overlay's layer, not `mine`: ownership is presumed for the whole
  // shared catalogue, so `mine` is true for agents this account never made. The layer is the
  // account's own directory, which is exactly what "my agents" means — and it is empty when
  // nobody is signed in, so the section simply does not render then.
  // STANDALONE APPS ARE NOT AGENTS EITHER — Agent Builder is a feature of the product, so it
  // gets its own nav row below and is filtered out of every list here. The rule comes from the
  // agent's own `[app] standalone` declaration (lib/standaloneApps), never from its id.
  const listable = listableAgents(agents)
  const surfaces = standaloneApps(agents)
  const myAgents = listable.filter((a) => a.layer === 'account' && a.id !== MAIN_AGENT_ID && !a.sample)
  const namedAgents = listable.filter(
    (a) => a.id !== MAIN_AGENT_ID && !a.sample && a.layer !== 'account',
  )
  const sampleAgents = listable.filter((a) => a.sample)

  const projectsActive = view === 'projects' || view === 'project'
  // THE CONTROL PLANE IS A PLACE, not a settings row. It governs the whole deployment — the
  // defaults every account inherits, who may sign in, where the money went — so it belongs in
  // the nav beside Projects rather than three clicks down inside one account's preferences.
  const admin = useIsAdmin()
  const orgs = useMyOrgs()

  // ---- collapsed icon rail --------------------------------------------------
  if (collapsed) {
    return (
      <aside className="sidebar sidebar--rail">
        <img className="brand-logo brand-logo--rail" src={logo} alt="" />
        <button className="rail-btn" title="expand sidebar" onClick={toggleSidebar}><PanelLeft size={17} /></button>
        <button className="rail-primary" title="new chat" onClick={() => newChat()}><SquarePen size={17} /></button>
        <button className="rail-btn" title="search chats" onClick={toggleSidebar}><Search size={17} /></button>
        <button className={`rail-btn ${projectsActive ? 'active' : ''}`} title="Projects" onClick={() => setView('projects')}><Folder size={17} /></button>
        <div className="rail-sep" />
        <button className="rail-btn" title="create agent" onClick={() => setNewAgent(true)}><UserPlus size={17} /></button>
        {namedAgents.map((a) => (
          <button
            key={a.id}
            className={`rail-agent ${a.id === viewedAgentId && view === 'agent' ? 'active' : ''}`}
            title={a.name || a.id}
            onClick={() => viewAgent(a.id)}
          >
            <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(a.name, a.id)}</span>
          </button>
        ))}
        <div className="rail-spacer" />
        <ProfileMenu variant="rail" />
        <button className="rail-btn" title="Agents" onClick={() => setView('myagents')}><LayoutGrid size={18} /></button>
        {admin && (
          <button
            className={`rail-btn ${view === 'admin' ? 'active' : ''}`}
            title="Admin"
            onClick={() => openAdminConsole(setView)}
          >
            <ShieldCheck size={17} />
          </button>
        )}
        <SettingsMenu variant="rail" />
        <button className="rail-btn" title="toggle theme" onClick={toggleTheme}>{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}</button>
        {newAgent && <NewAgentModal onClose={() => setNewAgent(false)} />}
      </aside>
    )
  }

  // ---- full sidebar ---------------------------------------------------------
  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-logo" src={logo} alt="" />
        <span className="brand-name">{flavor?.productName || 'agentd'}</span>
        <span className="live"><span className="live-dot" style={{ opacity: connection === 'open' ? 1 : 0.3 }} />live</span>
        <button className="icon-btn icon-btn--sm" title="collapse sidebar" onClick={toggleSidebar}><PanelLeft size={17} /></button>
      </div>

      {/* compact nav rows */}
      <div className="nav-rows">
        <NavRow icon={<SquarePen size={17} />} label="New chat" onClick={() => newChat()} title="New chat" />
        {searchOpen ? (
          <SearchBox
            className="search nav-search"
            value={query}
            onChange={setQuery}
            placeholder="Search chats"
            iconSize={16}
            autoFocus
            onBlur={() => { if (!query.trim()) setSearchOpen(false) }}
            onKeyDown={(e) => { if (e.key === 'Escape') { setQuery(''); setSearchOpen(false) } }}
          />
        ) : (
          <NavRow icon={<Search size={17} />} label="Search" onClick={() => setSearchOpen(true)} title="Search chats" />
        )}
        <NavRow icon={<Folder size={17} />} label="Projects" active={projectsActive} onClick={() => setView('projects')} title="Projects" />
        {/* The SHELF — the way to the full list of agents, and where agents are installed from.
            It is the ONLY agent entry point in this panel now that the inline "Agents" listing
            below has gone, so it stays in the nav rows rather than only in the rail/footer. */}
        <NavRow
          icon={<LayoutGrid size={17} />}
          label="Agents"
          active={view === 'myagents'}
          onClick={() => setView('myagents')}
          title="Agents — every agent you can use, open and share"
        />
        {/* THE ORGANIZATION — present exactly when the account belongs to one (memberships come
            from accounts, so an individual account never sees this). One org opens straight to
            its page — agents, members, seats; several open the overview to pick from. */}
        {orgs.length > 0 && (
          <NavRow
            icon={<Building2 size={17} />}
            label={orgs.length === 1 ? orgs[0].name : 'Organizations'}
            active={view === 'org'}
            onClick={() => viewOrg(orgs.length === 1 ? orgs[0].id : '')}
            title="Your organization — its agents, members, seats and credits"
          />
        )}
        {/* PRODUCT SURFACES — one row per agent that declares `[app] standalone`. Rendered from
            the roster, so Agent Builder appears on a desktop and Cloud Agent Builder on the web
            purely because each is offered there; neither is named here, and a third would show
            up by declaring the same thing. */}
        {surfaces.map((a) => (
          <NavRow
            key={a.id}
            icon={<Blocks size={17} />}
            label={a.app?.title || a.name || a.id}
            onClick={() => void launchStandaloneApp(a, openAgentApp)}
            title={`${a.app?.title || a.name} — ${a.tagline || 'open the app'}`}
          />
        ))}
        {admin && (
          <NavRow
            icon={<ShieldCheck size={17} />}
            label="Admin"
            active={view === 'admin'}
            onClick={() => openAdminConsole(setView)}
            title="Admin — deployment defaults, users, usage"
          />
        )}
      </div>

      {/* MY AGENTS — what the signed-in account created or installed into its own layer.
          Above the catalogue because they are the ones this person comes back for, and absent
          entirely when signed out — an empty "My agents" would read as everything being lost. */}
      {myAgents.length > 0 && (
        <>
          <SectionHead
            icon={<Users size={14} />}
            label="My agents"
            open={sectionOpen.mine}
            onToggle={() => toggleSection('mine')}
          />
          {sectionOpen.mine && (
            <div className="agents-list">
              {myAgents.map((a) => (
                <button
                  key={a.id}
                  className={`row ${a.id === viewedAgentId && view === 'agent' ? 'active' : ''}`}
                  title={a.name || a.id}
                  onClick={() => viewAgent(a.id)}
                >
                  <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>{agentInitials(a.name, a.id)}</span>
                  <span className="row-main">
                    <span className="row-title">{a.name || a.id}</span>
                    <span className="row-sub">{a.tagline || agentTag(a.id)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {/* NO INLINE "AGENTS" LISTING. Every agent on the machine used to be enumerated here; the
          full list now lives on the My Agents shelf (the nav row above, /agents), so the panel
          keeps chats and the account's own agents rather than duplicating the catalogue.
          Creating an agent moved with it — the "+" that opened NewAgentModal lived on this
          section head, and the rail's create button (above) is now the one in this panel. */}

      {/* SAMPLES — reference agents we ship. Collapsed by default and never mixed in above:
          they are here to be opened, read and run, not to pad the user's own list. */}
      {sampleAgents.length > 0 && (
        <>
          <SectionHead
            icon={<Users size={14} />}
            label="Samples"
            open={sectionOpen.samples}
            onToggle={() => toggleSection('samples')}
          />
          {sectionOpen.samples && (
            <div className="agents-list">
              {sampleAgents.map((a) => (
                <button
                  key={a.id}
                  className={`row ${a.id === viewedAgentId && view === 'agent' ? 'active' : ''}`}
                  title={`${a.name || a.id} — a reference implementation you can open and run`}
                  onClick={() => viewAgent(a.id)}
                >
                  <span className="avatar" style={{ background: agentColor(a.color, a.id) }}>
                    {agentInitials(a.name, a.id)}
                  </span>
                  <span className="row-main">
                    <span className="row-title">{a.name || a.id}</span>
                    <span className="row-sub">{a.tagline || 'sample'}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {/* RECENTS — ALL chats across every agent (agent dot + project badge) */}
      <div className="sidebar-scroll">
        <SectionHead
          icon={<History size={14} />}
          label="Recents"
          open={sectionOpen.recents}
          onToggle={() => toggleSection('recents')}
          extraClass="section-chats"
        />
        {sectionOpen.recents && (<>
          {chats.slice(0, 100).map((s) => (
            <SessionItem
              key={s.sessionId}
              session={s}
              active={view === 'chat' && s.sessionId === currentSessionKey}
              onOpen={() => void resumeSession(s.sessionId)}
              withAgentDot
              withProjectBadge
            />
          ))}
          {chats.length === 0 && (
            <div className="row-sub list-empty">
              {q ? 'no chats match' : 'no saved chats yet'}
            </div>
          )}
        </>)}
      </div>

      <div className="footer-nav">
        <ProfileMenu variant="footer" />
        {/* Whose keys pay for model calls — always on screen, click to switch. */}
        <RunModeBadge />
        <button className={`icon-btn footer-icon push-end ${view === 'myagents' ? 'active' : ''}`} title="Agents" onClick={() => setView('myagents')}>
          <LayoutGrid size={17} />
        </button>
        <SettingsMenu variant="footer" />
        <button className="icon-btn footer-icon" title="toggle theme" onClick={toggleTheme}>
          {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </div>

      {newAgent && <NewAgentModal onClose={() => setNewAgent(false)} />}
    </aside>
  )
}

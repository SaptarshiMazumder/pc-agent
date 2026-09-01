/* The window: three regions — a section rail, the content, and the agent beside it.
 *
 * THE DASHBOARD TEMPLATE. Same base skeleton as the chat template — everything imported below
 * except `Dashboard`, `AgentPanel` and `components/widgets/*` is the base's file, landed in this
 * tree at creation. What this template changes is the SHAPE: the rail's middle is your SECTIONS
 * instead of a chat list, the main area shows the selected section's content, and the
 * conversation lives in a permanent panel on the right — you never choose between looking at the
 * work and talking about it.
 *
 * THIS FILE IS YOURS. The sections above all: `SECTIONS` below and `PANELS` in
 * components/Dashboard.tsx are the two lists an agent fills in. What must NOT be rewritten is
 * anything under `src/common/` (the .css there IS yours): sign-in, credits, settings and
 * organizations are the same four screens in every agent, and `validate_agent` compares your
 * copies against the source.
 *
 * THE SHARED SCREENS REPLACE THE CONTENT REGION ONLY. The rail and the agent panel sit outside
 * that branch, so opening Settings does not take the agent away from you. A shared screen that
 * took over the whole window would be a different product.
 */

import { useEffect } from 'react'
import { LayoutGrid, Plus } from 'lucide-react'

import { AGENT_ID, useClient } from './agentd/client'
import { handleRunEvent } from './agentd/run-events'
import { listSessions } from './agentd/sessions'
import { useApp } from './state/store'

import AgentPanel from './components/AgentPanel'
import Dashboard from './components/Dashboard'
import { PLACEHOLDER_SECTIONS } from './components/sections/PlaceholderSections'
import type { SectionSpec } from './components/sections/section'
import { Sidebar } from './components/Sidebar'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'
import { Settings } from './common/settings/Settings'

/** What this agent is called — the rail's brand and the sign-in card's heading. */
const AGENT_NAME = 'This agent'

/* THE SECTIONS — the rail's middle, and what the main area shows. Add one: an entry here, a
 * branch in the main area below. The mock most agents start from is a file-manager shape
 * (Desktop, Documents, …); yours are whatever this agent's work divides into.
 *
 * `headline` IS THE POINT OF THE SECTION, not a greeting. "Spend is up 22% on last week" earns
 * the space; "Welcome back" does not. Until a real one can be computed, say what the section is
 * for — and replace it the moment the numbers can speak. */
const SECTIONS: SectionSpec[] = [
  {
    id: 'overview',
    label: 'Overview',
    icon: <LayoutGrid size={16} strokeWidth={1.8} />,
    headline: 'Everything at a glance',
    blurb:
      'The panels below are placeholders — see components/widgets/README.md. Replace them with ' +
      'panels over this agent’s own tools, and this line with what the numbers actually say.',
    render: ({ client, connected }) => <Dashboard client={client} connected={connected} />,
  },

  /* THREE EXAMPLE SCREENS — a table, a pair of charts, a queue. DELETE THIS LINE AND THE FILE
     once you know what screens this agent has; keep whichever is closest and make it real. They
     are here to show that a section is a whole screen, not to be the screens. */
  ...PLACEHOLDER_SECTIONS,
]

/* THE FRONT DOOR IS THE CONTENT, not a conversation. Set before the first render — an effect
   would paint a frame of something else first, which reads as a glitch on every open. */
useApp.setState({ view: 'overview' })

export default function App() {
  const { client, status } = useClient()
  const connected = status === 'open'

  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const newSession = useApp((s) => s.newSession)
  const currentKey = useApp((s) => s.currentSessionKey)
  /* ONE SOURCE OF TRUTH for which screen is open. It used to be `view` AND a separate
     `section`, which could disagree — clicking Credits left the old section marked active in the
     rail. The rail sets `view`; the section is whichever spec matches it. */

  const account = useAuth(client!)

  /* ONE SUBSCRIPTION, TORN DOWN ON RECONNECT — see the chat template for why. */
  useEffect(() => {
    if (!client) return
    const off = client.on('chat.event', (payload: any) => handleRunEvent(payload))
    return () => off()
  }, [client])

  /* A RECONNECT NO LONGER MEANS THE RUN IS DEAD. The daemon keeps a run alive when its window
     drops and reaps it only if nobody returns, so this ASKS rather than assuming: `chat.status`
     answers and re-attaches this window in one call. Still running means keep streaming; ended
     means it finished or was reaped while we were away. An older daemon has no `chat.status`, so
     there the old assumption stands — which is what the message below is for. */
  useEffect(() => {
    if (!connected || !client) return
    const { sessions } = useApp.getState()
    for (const key of Object.keys(sessions)) {
      if (!sessions[key].running) continue
      void (async () => {
        let running = false
        try {
          const st = (await client.request('chat.status', { sessionKey: key })) as {
            running?: boolean
          }
          running = !!st?.running
        } catch {
          /* older daemon — no way to ask; assume the run is gone, as before */
        }
        if (running) return
        const { sessions: now, patch, append } = useApp.getState()
        if (!now[key]?.running) return
        patch(key, { running: false })
        append(key, [
          {
            kind: 'system',
            tone: 'error',
            text: 'The daemon restarted mid-run, so this run is gone. Resend your last message to continue.',
            ts: Date.now(),
          },
        ])
      })()
    }
  }, [connected, client])

  /* A session for the panel to type into. show=false: booting must not change the screen. */
  useEffect(() => {
    if (!connected || !client) return
    if (!currentKey) newSession(false)
    void listSessions(client).then((rows) => useApp.getState().setChats(rows))
  }, [connected, client, currentKey, newSession])

  if (account.wantsSignIn) return <SignIn product={AGENT_NAME} onDone={account.signedIn} />

  /* The shared screens replace the CONTENT region; the agent panel stays. */
  const shared =
    view === 'credits' ? (
      <Credits agentId={AGENT_ID} />
    ) : view === 'orgs' ? (
      <OrgView client={client ?? undefined} />
    ) : view === 'settings' ? (
      client && <Settings client={client} agentId={AGENT_ID} />
    ) : null

  const current = SECTIONS.find((s) => s.id === view) || SECTIONS[0]

  return (
    <div className="shell shell--workbench">
      <LiveReload client={client ?? undefined} />
      <Sidebar
        view={view}
        onView={setView}
        onNewChat={() => newSession(false)}
        account={account}
        client={client ?? undefined}
        status={status}
        name={AGENT_NAME}
        /* Conversations live in the top bar and the agent panel here, and this template has no
           full-width chat view — so the rail shows neither a second New-conversation button nor
           a destination that would select a screen this window does not have. */
        showPrimary={false}
        showConversation={false}
        /* THE SECTIONS ARE DESTINATIONS, not a list under the account. `extraDestinations`
           renders them above the shared three, which is the order a workbench reads in: where I
           work first, my account second. */
        extraDestinations={SECTIONS.map((s) => ({ id: s.id, label: s.label, icon: s.icon }))}
        counts={Object.fromEntries(
          SECTIONS.filter((s) => s.count).map((s) => [s.id, s.count as string]),
        )}
        groupLabel="Sections"
        sharedGroupLabel="Account"
        middle={null}
      />

      <main className="main">
        {/* THE TOP BAR is its own card, above whatever the content region is showing — so the
            daemon's state and the way to start a conversation are constant, including while a
            shared screen is open. */}
        <div className="topbar">
          <span className={`daemon-chip${connected ? ' is-live' : ''}`}>
            <span className={`live-dot${connected ? ' is-live' : ''}`} />
            {connected ? 'daemon connected' : status}
          </span>
          <span className="topbar-spacer" />
          <button className="topbar-primary" onClick={() => newSession(false)}>
            <Plus size={15} strokeWidth={2.2} />
            New conversation
          </button>
        </div>

        {shared ?? (
          <div className="content-card">
            {/* The breadcrumb line: where in the agent's world the content is. One crumb until
                your sections have depth — then make it a real path. */}
            <div className="workbench-crumbs">
              <span className="crumb">{AGENT_NAME}</span>
              <span className="crumb-sep">›</span>
              <span className="crumb is-current">{current.label}</span>
            </div>

            <header className="section-head">
              <span className="section-head-ico">{current.icon}</span>
              <div className="section-head-text">
                <h1 className="section-title">{current.headline}</h1>
                <p className="section-blurb">{current.blurb}</p>
              </div>
            </header>

            {client && current.render({ client, connected })}
          </div>
        )}
      </main>

      <AgentPanel client={client} connected={connected} />
    </div>
  )
}

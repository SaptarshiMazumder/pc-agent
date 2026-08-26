/* The window: three regions — a section rail, the content, and the agent beside it.
 *
 * THE DASHBOARD TEMPLATE. Same base skeleton as the chat template — everything imported below
 * except `Dashboard` and `AgentPanel` is the base's file, landed in this tree at creation. What
 * this template changes is the SHAPE: the rail's middle is your SECTIONS instead of a chat list,
 * the main area shows the selected section's content, and the conversation lives in a permanent
 * panel on the right — you never choose between looking at the work and talking about it.
 *
 * THIS FILE IS YOURS. The sections above all: `SECTIONS` below and `PANELS` in
 * components/Dashboard.tsx are the two lists an agent fills in. What must NOT be rewritten is
 * anything under `src/common/` (the .css there IS yours): sign-in, credits, settings and
 * organizations are the same four screens in every agent, and `validate_agent` compares your
 * copies against the source.
 */

import { useEffect, useState } from 'react'
import { LayoutGrid } from 'lucide-react'

import { AGENT_ID, useClient } from './agentd/client'
import { handleRunEvent } from './agentd/run-events'
import { listSessions } from './agentd/sessions'
import { useApp } from './state/store'

import AgentPanel from './components/AgentPanel'
import Dashboard from './components/Dashboard'
import { Sidebar } from './components/Sidebar'

import Credits from './common/credits/Credits'
import LiveReload from './common/dev/LiveReload'
import SignIn from './common/auth/SignIn'
import { useAuth } from './common/auth/useAuth'
import OrgView from './common/orgs/OrgView'
import { Settings } from './common/settings/Settings'

/* THE SECTIONS — the rail's middle, and what the main area shows. Add one: an entry here, a
 * branch in the main area below. The mock most agents start from is a file-manager shape
 * (Desktop, Documents, …); yours are whatever this agent's work divides into. */
const SECTIONS: { id: string; label: string; icon: JSX.Element }[] = [
  { id: 'overview', label: 'Overview', icon: <LayoutGrid size={16} /> },
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
  const [section, setSection] = useState('overview')

  const account = useAuth(client!)

  /* ONE SUBSCRIPTION, TORN DOWN ON RECONNECT — see the chat template for why. */
  useEffect(() => {
    if (!client) return
    const off = client.on('chat.event', (payload: any) => handleRunEvent(payload))
    return () => off()
  }, [client])

  /* A RECONNECT MEANS EVERY IN-FLIGHT RUN IS DEAD — cleared, with a line saying why. */
  useEffect(() => {
    if (!connected) return
    const { sessions, patch, append } = useApp.getState()
    for (const key of Object.keys(sessions)) {
      if (!sessions[key].running) continue
      patch(key, { running: false })
      append(key, [
        {
          kind: 'system',
          tone: 'error',
          text: 'The daemon restarted mid-run, so this run is gone. Resend your last message to continue.',
          ts: Date.now(),
        },
      ])
    }
  }, [connected])

  /* A session for the panel to type into. show=false: booting must not change the screen. */
  useEffect(() => {
    if (!connected || !client) return
    if (!currentKey) newSession(false)
    void listSessions(client).then((rows) => useApp.getState().setChats(rows))
  }, [connected, client, currentKey, newSession])

  if (account.wantsSignIn) return <SignIn product="This agent" onDone={account.signedIn} />

  /* The shared screens replace the CONTENT region; the agent panel stays. */
  const shared =
    view === 'credits' ? (
      <Credits agentId={AGENT_ID} />
    ) : view === 'orgs' ? (
      <OrgView client={client ?? undefined} />
    ) : view === 'settings' ? (
      client && <Settings client={client} agentId={AGENT_ID} />
    ) : null

  return (
    <div className="shell shell--workbench">
      <LiveReload client={client ?? undefined} />
      <Sidebar
        view={view}
        onView={setView}
        onNewChat={() => newSession(false)}
        account={account}
        status={status}
        middle={
          <div className="nav-rows">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                className={`nav-row ${view === s.id && section === s.id ? 'on' : ''}`}
                onClick={() => {
                  setSection(s.id)
                  setView(s.id)
                }}
              >
                {s.icon}
                <span className="nav-row-label">{s.label}</span>
              </button>
            ))}
          </div>
        }
      />

      <main className="main">
        {shared ?? (
          <>
            {/* The breadcrumb line: where in the agent's world the content is. One crumb until
                your sections have depth — then make it a real path. */}
            <div className="workbench-crumbs">
              <span className="crumb">{SECTIONS.find((s) => s.id === section)?.label || 'Overview'}</span>
            </div>
            {client && <Dashboard client={client} connected={connected} />}
          </>
        )}
      </main>

      <AgentPanel client={client} connected={connected} />
    </div>
  )
}

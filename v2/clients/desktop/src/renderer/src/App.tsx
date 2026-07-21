import { useEffect } from 'react'

import AccountView from './components/AccountView'
import AgentView from './components/AgentView'
import Canvas from './components/Canvas'
import ChatView from './components/ChatView'
import DataSourcesView from './components/DataSourcesView'
import ProjectsView from './components/ProjectsView'
import ProjectView from './components/ProjectView'
import SettingsView from './components/SettingsView'
import Sidebar from './components/Sidebar'
import SignIn from './components/SignIn'
import StoreView from './components/StoreView'
import SubscriptionView from './components/SubscriptionView'
import { isAccountsMode, signOut, useAuthSession } from './lib/auth'
import { installSoftScroll } from './lib/softScroll'
import { useApp } from './state/store'

export default function App() {
  const bootstrap = useApp((state) => state.bootstrap)
  const view = useApp((state) => state.view)
  const connection = useApp((state) => state.connection)
  const supervisor = useApp((state) => state.supervisor)

  const session = useAuthSession()
  const needSignIn = isAccountsMode() && !session

  useEffect(() => {
    // In accounts mode, don't connect until signed in (no token to present yet). Once signed in,
    // needSignIn flips false and bootstrap runs. Desktop/no-accounts: runs once on mount.
    if (!needSignIn) void bootstrap()
  }, [bootstrap, needSignIn])

  // app-wide soft scroll edges: auto-applies the fade to every scroll container (any page)
  useEffect(() => installSoftScroll(), [])

  if (needSignIn) return <SignIn />

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        {session && (
          <div className="acct-chip" title={`Signed in as ${session.email}`}>
            <span className="acct-email">{session.email}</span>
            <button
              className="acct-signout"
              type="button"
              title="Sign out"
              onClick={() => {
                signOut()
                location.reload() // drop the account-scoped connection cleanly
              }}
            >
              Sign out
            </button>
          </div>
        )}
        {connection !== 'open' && (
          <div className={`banner ${supervisor.phase === 'failed' ? 'banner-error' : ''}`}>
            {supervisor.phase === 'failed'
              ? supervisor.message
              : connection === 'closed'
                ? 'connection lost — reconnecting…'
                : supervisor.phase === 'running'
                  ? 'connecting to agentd…'
                  : supervisor.message}
          </div>
        )}
        {view === 'chat' && <ChatView />}
        {view === 'store' && <StoreView />}
        {view === 'settings' && <SettingsView />}
        {view === 'datasources' && <DataSourcesView />}
        {view === 'account' && <AccountView />}
        {view === 'subscription' && <SubscriptionView />}
        {view === 'projects' && <ProjectsView />}
        {view === 'project' && <ProjectView />}
        {view === 'agent' && <AgentView />}
      </main>
      <Canvas />
    </div>
  )
}

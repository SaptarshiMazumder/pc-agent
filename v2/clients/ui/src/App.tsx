import { useEffect } from 'react'

import AccountView from './components/AccountView'
import AgentView from './components/AgentView'
import Canvas from './components/Canvas'
import ChatView from './components/ChatView'
import DataSourcesView from './components/DataSourcesView'
import Launcher from './components/Launcher'
import ProjectsView from './components/ProjectsView'
import ProjectView from './components/ProjectView'
import SettingsView from './components/SettingsView'
import Sidebar from './components/Sidebar'
import SignIn from './components/SignIn'
import StoreView from './components/StoreView'
import SubscriptionView from './components/SubscriptionView'
import { isAccountsMode, useAuthSession } from './lib/auth'
import { useMode } from './lib/mode'
import { isDesktop } from './lib/platform'
import { installRum } from './lib/rum'
import { installSoftScroll } from './lib/softScroll'
import { useApp } from './state/store'

export default function App() {
  const bootstrap = useApp((state) => state.bootstrap)
  const applyMode = useApp((state) => state.applyMode)
  const view = useApp((state) => state.view)
  const connection = useApp((state) => state.connection)
  const supervisor = useApp((state) => state.supervisor)

  const session = useAuthSession()
  const mode = useMode()

  // Desktop uses the Local/Cloud launcher; the web build keeps the plain accounts sign-in gate.
  const webNeedsSignIn = !isDesktop && isAccountsMode() && !session
  const cloudNeedsSignIn = isDesktop && mode === 'cloud' && !session

  useEffect(() => {
    // Connect to the (local) daemon unless the WEB sign-in gate is blocking it. On desktop the
    // daemon is local (machine token), so it connects regardless of mode/sign-in — the launcher
    // and cloud sign-in are overlays on top of a live connection.
    if (!webNeedsSignIn) void bootstrap()
  }, [bootstrap, webNeedsSignIn])

  useEffect(() => {
    // Whenever the mode or session changes (and the connection is up), re-assert Local/Cloud on
    // the daemon (platform.connect / platform.disconnect) and refresh platform status.
    if (isDesktop && connection === 'open') void applyMode()
  }, [applyMode, mode, session, connection])

  // app-wide soft scroll edges: auto-applies the fade to every scroll container (any page)
  useEffect(() => installSoftScroll(), [])

  // Browser RUM (5.3). No-op on desktop (the daemon's opt-in uploader owns that surface) and on
  // any build without an ingest URL, so this line costs nothing where it does not apply.
  useEffect(() => installRum(), [])

  // Desktop startup gate: pick Local or Cloud first.
  if (isDesktop && !mode) return <Launcher />
  if (webNeedsSignIn || cloudNeedsSignIn) return <SignIn />

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
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

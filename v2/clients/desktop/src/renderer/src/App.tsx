import { useEffect } from 'react'

import AccountView from './components/AccountView'
import Canvas from './components/Canvas'
import ChatView from './components/ChatView'
import DataSourcesView from './components/DataSourcesView'
import SettingsView from './components/SettingsView'
import Sidebar from './components/Sidebar'
import StoreView from './components/StoreView'
import SubscriptionView from './components/SubscriptionView'
import { installSoftScroll } from './lib/softScroll'
import { useApp } from './state/store'

export default function App() {
  const bootstrap = useApp((state) => state.bootstrap)
  const view = useApp((state) => state.view)
  const connection = useApp((state) => state.connection)
  const supervisor = useApp((state) => state.supervisor)

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  // app-wide soft scroll edges: auto-applies the fade to every scroll container (any page)
  useEffect(() => installSoftScroll(), [])

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
      </main>
      <Canvas />
    </div>
  )
}

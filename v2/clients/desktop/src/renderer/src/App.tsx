import { useEffect } from 'react'

import ChatView from './components/ChatView'
import SettingsView from './components/SettingsView'
import Sidebar from './components/Sidebar'
import StoreView from './components/StoreView'
import { useApp } from './state/store'

export default function App() {
  const bootstrap = useApp((state) => state.bootstrap)
  const view = useApp((state) => state.view)
  const connection = useApp((state) => state.connection)
  const supervisor = useApp((state) => state.supervisor)

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

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
      </main>
    </div>
  )
}

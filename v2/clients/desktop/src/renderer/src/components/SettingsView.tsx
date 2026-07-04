import { useApp } from '../state/store'

export default function SettingsView() {
  const flavor = useApp((state) => state.flavor)
  const hello = useApp((state) => state.hello)
  const supervisor = useApp((state) => state.supervisor)
  const connection = useApp((state) => state.connection)
  const notifications = useApp((state) => state.notifications)

  const rows: [string, string][] = [
    ['Product', `${flavor?.productName || 'agentd'} (shell ${flavor?.version || '?'})`],
    ['Daemon', supervisor.message],
    ['Connection', connection],
    ['Gateway version', hello?.version || '—'],
    ['Model', hello?.model || '—'],
    ['Default agent', hello?.agentId || '—'],
    ['Workspace', hello?.workspace || '—'],
    ['Store', hello?.storeEnabled ? (hello?.registryConfigured ? 'enabled' : 'enabled (no registry configured)') : 'disabled']
  ]

  return (
    <div className="settings">
      <h1>Settings</h1>
      <div className="kv">
        {rows.map(([key, value]) => (
          <div className="kv-row" key={key}>
            <span className="kv-key">{key}</span>
            <span className="kv-value">{value}</span>
          </div>
        ))}
      </div>
      <p className="chat-sub pad-top">
        Models, API keys, and plugin knobs live in the daemon's config (~/.agentd/config.json — or the
        checkout's agentd.config.json in dev). Edit there and restart the daemon; this shell is just a client.
      </p>

      {notifications.length > 0 && (
        <>
          <div className="section-label pad-top">Notifications</div>
          {notifications.map((notification) => (
            <div className="installed-row" key={notification.id}>
              <span className="row-title">
                [{notification.kind}] {notification.text}
              </span>
              <span className="row-sub">
                {notification.at} · {notification.detail}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

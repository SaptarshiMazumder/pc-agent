import { useState } from 'react'
import { Sun, Moon } from 'lucide-react'

import { useApp } from '../state/store'

/**
 * Note: reasoning / computer-use / notifications are shown here as client-side UI
 * (the real values live in the daemon config). Theme is wired to the store.
 */
export default function SettingsView() {
  const flavor = useApp((s) => s.flavor)
  const hello = useApp((s) => s.hello)
  const supervisor = useApp((s) => s.supervisor)
  const connection = useApp((s) => s.connection)
  const theme = useApp((s) => s.theme)
  const toggleTheme = useApp((s) => s.toggleTheme)

  const [reasoning, setReasoning] = useState('medium')
  const [computer, setComputer] = useState(false)
  const [notify, setNotify] = useState(true)

  const rows: [string, string][] = [
    ['Product', `${flavor?.productName || 'agentd'} · shell ${flavor?.version || '?'}`],
    ['Daemon', supervisor.message],
    ['Connection', connection],
    ['Gateway', hello?.version || '—'],
    ['Model', hello?.model || '—'],
    ['Default agent', hello?.agentId || '—'],
    ['Workspace', hello?.workspace || '—'],
    ['Registry', hello?.registryUrl || `local · ${hello?.localRegistryDir || '?'}`]
  ]

  return (
    <div className="settings">
      <div className="settings-inner">
        <div className="page-title" style={{ marginBottom: 20 }}>Settings</div>

        <div className="settings-section">Preferences</div>
        <div className="settings-card">
          <div className="settings-row">
            <div className="settings-label"><div className="k">Appearance</div><div className="d">Light or dark shell.</div></div>
            <div className="seg">
              <button className={theme === 'dark' ? 'on' : ''} onClick={() => theme !== 'dark' && toggleTheme()}><Moon size={14} />Dark</button>
              <button className={theme === 'light' ? 'on' : ''} onClick={() => theme !== 'light' && toggleTheme()}><Sun size={14} />Light</button>
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-label"><div className="k">Reasoning effort</div><div className="d">Depth of the agent’s thinking pass.</div></div>
            <div className="seg">
              {['off', 'low', 'medium', 'high'].map((r) => (
                <button key={r} className={reasoning === r ? 'on' : ''} onClick={() => setReasoning(r)}>{r}</button>
              ))}
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-label"><div className="k">Computer use</div><div className="d">Let the agent operate the desktop GUI. Off by default.</div></div>
            <button className={`switch ${computer ? 'on' : ''}`} onClick={() => setComputer((v) => !v)} />
          </div>
          <div className="settings-row">
            <div className="settings-label"><div className="k">Desktop notifications</div><div className="d">Ping me when a long run finishes.</div></div>
            <button className={`switch ${notify ? 'on' : ''}`} onClick={() => setNotify((v) => !v)} />
          </div>
        </div>

        <div className="settings-section">Runtime</div>
        <div className="kv-card">
          {rows.map(([k, v]) => (
            <div className="kv-row" key={k}>
              <span className="kv-key">{k}</span>
              <span className="kv-val">{v}</span>
            </div>
          ))}
        </div>

        <p className="settings-note">
          Models, API keys and plugin knobs live in the daemon’s config (<code>~/.agentd/config.json</code>).
          Edit there and restart the daemon — this shell is the client.
        </p>
      </div>
    </div>
  )
}

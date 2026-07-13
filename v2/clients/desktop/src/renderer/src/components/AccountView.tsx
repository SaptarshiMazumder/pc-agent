import { User, LogIn } from 'lucide-react'

import { useApp } from '../state/store'
import PageShell from './PageShell'

/** Account — a local-profile stub today; cloud sign-in/sync lands with the accounts backend. */
export default function AccountView() {
  const hello = useApp((s) => s.hello)
  const flavor = useApp((s) => s.flavor)

  const rows: [string, string][] = [
    ['Product', flavor?.productName || 'agentd'],
    ['Assistant', hello?.agentName || '—'],
    ['Workspace', hello?.workspace || '—'],
    ['Signed in as', 'Local — no account']
  ]

  return (
    <PageShell title="Account" sub="Your local profile. Cloud sign-in is coming soon.">
        <div className="account-hero">
          <div className="account-avatar"><User size={30} /></div>
          <div>
            <div className="account-name">Local user</div>
            <div className="account-role">Running {flavor?.productName || 'agentd'} on this machine</div>
          </div>
        </div>

        <div className="settings-group">
          <div className="settings-section">Profile</div>
          <div className="kv-card">
            {rows.map(([k, v]) => (
              <div className="kv-row" key={k}>
                <span className="kv-key">{k}</span>
                <span className="kv-val">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="settings-group">
          <div className="settings-section">Sign in</div>
          <div className="settings-card">
            <div className="settings-row">
              <div className="settings-label">
                <div className="k">Account &amp; sync</div>
                <div className="d">Sign in to sync agents, settings and subscriptions across your devices.</div>
              </div>
              <button className="btn" disabled>
                <LogIn size={15} />Coming soon
              </button>
            </div>
          </div>
        </div>
    </PageShell>
  )
}

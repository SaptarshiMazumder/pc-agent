import { useEffect, useState } from 'react'
import { User, LogIn, RefreshCw } from 'lucide-react'

import { fetchCredits, useAuthSession, type Credits } from '../lib/auth'
import { useApp } from '../state/store'
import PageShell from './PageShell'

/** Account — the signed-in platform profile (hosted flavors) or the local-profile stub. */
export default function AccountView() {
  const hello = useApp((s) => s.hello)
  const flavor = useApp((s) => s.flavor)
  const session = useAuthSession()

  // CREDITS. Read straight from the accounts service with the session token — the daemon is not
  // in this path at all, because the balance is the account's business and not the machine's.
  const [credits, setCredits] = useState<Credits | null>(null)
  const [loading, setLoading] = useState(false)

  async function refresh(): Promise<void> {
    setLoading(true)
    setCredits(await fetchCredits())
    setLoading(false)
  }

  useEffect(() => {
    if (session) void refresh()
    else setCredits(null)
    // Re-read whenever the identity changes; a stale balance from a previous account would be
    // worse than showing none.
  }, [session?.accountId])

  const proxyStatus = hello?.platform?.modelProxy || hello?.platform?.modelGateway
  const hosted = !!proxyStatus?.enabled
  const keysLabel = hosted
    ? 'Platform keys (included with your account)'
    : 'Your own keys (set in Settings)'

  const rows: [string, string][] = [
    ['Product', flavor?.productName || 'agentd'],
    ['Assistant', hello?.agentName || '—'],
    ['Workspace', hello?.workspace || '—'],
    ['Signed in as', session ? session.email : 'Local — no account'],
    ['Model access', keysLabel]
  ]

  return (
    <PageShell
      title="Account"
      sub={session ? 'Your platform account on this device.' : 'Your local profile. Cloud sign-in is coming soon.'}
    >
        <div className="account-hero">
          <div className="account-avatar"><User size={30} /></div>
          <div>
            <div className="account-name">{session ? session.email : 'Local user'}</div>
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

        {session && hosted && (
          <div className="settings-group">
            <div className="settings-section">Credits</div>
            <div className="kv-card">
              <div className="kv-row">
                <span className="kv-key">Balance</span>
                <span className="kv-val">
                  {credits ? `${credits.creditsRemaining.toLocaleString()} credits` : loading ? 'checking…' : 'unavailable'}
                </span>
              </div>
              {credits && credits.creditsRemaining === 0 && (
                <div className="kv-row">
                  <span className="kv-key">Status</span>
                  <span className="kv-val">Out of credits — messages will be refused until you top up</span>
                </div>
              )}
              {credits?.fundingSource && (
                <div className="kv-row">
                  <span className="kv-key">Funded by</span>
                  <span className="kv-val">
                    {credits.fundingSource === 'agent_subscription' ? 'An agent subscription' : 'Your platform balance'}
                    {credits.creditClass === 'promotional' ? ' (promotional)' : ''}
                  </span>
                </div>
              )}
              {credits?.modelTierMax && (
                <div className="kv-row">
                  <span className="kv-key">Model limit</span>
                  <span className="kv-val">Up to the “{credits.modelTierMax}” tier on this plan</span>
                </div>
              )}
              {!!credits?.expiresAt && (
                <div className="kv-row">
                  <span className="kv-key">Expires</span>
                  <span className="kv-val">{new Date(credits.expiresAt * 1000).toLocaleDateString()}</span>
                </div>
              )}
              <div className="settings-row">
                <div className="settings-label">
                  <div className="d">
                    Credits are spent per model call — a cheap model costs a fraction of a premium one.
                  </div>
                </div>
                <button className="btn" type="button" onClick={() => void refresh()} disabled={loading}
                        title="Re-read your balance from the platform">
                  <RefreshCw size={15} />Refresh
                </button>
              </div>
            </div>
          </div>
        )}

        {!session && (
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
        )}
    </PageShell>
  )
}

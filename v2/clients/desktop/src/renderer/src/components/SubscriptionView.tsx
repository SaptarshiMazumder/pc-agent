import { Check, Sparkles, Users } from 'lucide-react'

import { useApp } from '../state/store'

/** Subscription — local/free today; paid tiers are placeholders until billing exists. */
export default function SubscriptionView() {
  const flavor = useApp((s) => s.flavor)
  const soon: { name: string; desc: string; icon: React.ReactNode }[] = [
    { name: 'Pro', desc: 'Cloud sync, hosted models, and priority support.', icon: <Sparkles size={20} /> },
    { name: 'Team', desc: 'Shared agents, seats, and admin controls.', icon: <Users size={20} /> }
  ]

  return (
    <div className="settings">
      <div className="settings-inner settings-wide">
        <div className="settings-head">
          <div className="settings-head-titles">
            <div className="page-title">Subscription</div>
            <div className="page-sub">
              You’re running {flavor?.productName || 'agentd'} locally — no subscription needed. Paid plans
              arrive with cloud sync.
            </div>
          </div>
        </div>

        <div className="settings-group">
          <div className="settings-section">Current plan</div>
          <div className="plan-card">
            <div className="plan-top">
              <span className="plan-name">Local</span>
              <span className="badge free">Active</span>
            </div>
            <div className="plan-price">Free<span> · self-hosted</span></div>
            <ul className="plan-feats">
              <li><Check size={14} />Unlimited local chats &amp; agents</li>
              <li><Check size={14} />Bring your own model API keys</li>
              <li><Check size={14} />Every tool &amp; plugin included</li>
            </ul>
          </div>
        </div>

        <div className="settings-group">
          <div className="settings-section">Coming soon</div>
          <div className="cards">
            {soon.map((p) => (
              <div className="card" key={p.name}>
                <div className="card-top">
                  <div className="card-icon">{p.icon}</div>
                  <div>
                    <div className="card-name">{p.name}</div>
                    <div className="card-by">planned</div>
                  </div>
                </div>
                <p className="card-desc">{p.desc}</p>
                <div className="card-actions">
                  <button className="btn" disabled>Coming soon</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

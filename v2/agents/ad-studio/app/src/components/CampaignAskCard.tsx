/* `campaign_ask` in the thread: the gate's questions as the agent asked them, each with the default
 * it will take. Read only — the answer is given in the studio beside the chat (or typed), so the
 * card points there instead of growing a second set of controls that could disagree. */

import { HelpCircle } from 'lucide-react'

import type { ToolItem } from '../agentd/chat'

export function CampaignAskCard({ item, answered }: { item: ToolItem; answered: boolean }) {
  const args = item.args as { campaign?: string; questions?: { about?: string; question?: string; default?: string }[] }
  const questions = Array.isArray(args.questions) ? args.questions : []
  return (
    <div className={`ask-card${answered ? ' answered' : ''}`}>
      <div className="ask-head">
        <HelpCircle size={15} />
        <span className="ask-title">Your call</span>
        <span className="ask-sub">{args.campaign}</span>
        <span className="ask-state">{answered ? 'answered' : 'waiting for you'}</span>
      </div>
      <ul className="ask-list">
        {questions.map((q, i) => (
          <li key={i}>
            <span className="ask-about">{q.about}</span>
            <span className="ask-q">{q.question}</span>
            <span className="ask-default">{q.default}</span>
          </li>
        ))}
      </ul>
      {!answered && <div className="ask-foot">Decide in the studio on the right, then Send answer — or type it here.</div>}
    </div>
  )
}

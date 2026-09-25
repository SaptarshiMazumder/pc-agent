/* A workflow as the landing page sells it: three renders it made, a name, and what goes in and
 * what comes out — in words, never nodes.
 *
 * ON THIS PAGE THE BUTTON OPENS SIGN-IN, like every other control here. The daemon refuses an
 * anonymous run, so a card that pretended to start one would only move the refusal somewhere
 * more confusing.
 */

import { LandingShot } from './LandingShot'
import { MediaKindTag } from '../media/MediaKindTag'
import type { StarterWorkflow } from './landing-content'

export function LandingWorkflowCard({
  workflow,
  action,
  onAction,
}: {
  workflow: StarterWorkflow
  action: string
  onAction: () => void
}): JSX.Element {
  const [first, second, third] = workflow.shots
  return (
    <article className="lp-wf">
      <div className="lp-wf-strip">
        <LandingShot src={first} alt={workflow.title} className="shot-fill" />
        <LandingShot src={second} alt="" className="shot-fill" />
        <LandingShot src={third} alt="" className="shot-fill" />
      </div>
      <div className="lp-wf-body">
        <MediaKindTag kind={workflow.kind} />
        <h3 className="lp-wf-title">{workflow.title}</h3>
        <p className="lp-wf-steps">
          {workflow.steps.map(([bold, rest]) => (
            <span key={bold}>
              <b>{bold}</b>
              {rest}
            </span>
          ))}
        </p>
        <button className="lp-btn lp-btn-sm" onClick={onAction}>
          {action}
        </button>
      </div>
    </article>
  )
}

export default LandingWorkflowCard

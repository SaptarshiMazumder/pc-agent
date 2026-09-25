/* "One setup. Endless runs." — one pet-portrait workflow, run over four different pets.
 *
 * THE REUSE ARGUMENT, PROVEN. Each tile is a real pair: the photo that went in (inset) and the
 * portrait that came out. Swap the photo, change the style line, run it again — which is exactly
 * what "make it once, run it forever" means, shown rather than claimed.
 */

import { LandingShot } from './LandingShot'
import { LandingWorkflowCard } from './LandingWorkflowCard'
import type { STYLE_RUNS, StarterWorkflow } from './landing-content'

export function LandingStyleReuse({
  workflow,
  runs,
  onStart,
}: {
  workflow: StarterWorkflow
  runs: typeof STYLE_RUNS
  onStart: () => void
}): JSX.Element {
  return (
    <div className="lp-reuse">
      <LandingWorkflowCard workflow={workflow} action="Build one like it" onAction={onStart} />
      <div className="lp-reuse-runs">
        {runs.map((r, i) => (
          <figure key={r.after} className="lp-tile lp-style-run">
            <LandingShot src={r.after} alt={`${r.name}, ${r.style} portrait`} className="shot-fill" />
            <span className="lp-style-before">
              <LandingShot src={r.before} alt={`${r.name}, original photo`} className="shot-fill" />
            </span>
            <span className="lp-tile-br">
              run {i + 1} · {r.style}
            </span>
          </figure>
        ))}
      </div>
    </div>
  )
}

export default LandingStyleReuse

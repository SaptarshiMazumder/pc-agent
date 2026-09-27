/* "Watch a workflow work" — real pipelines, end to end: what went in, and each thing that came out.
 *
 * WHY THIS SECTION. A grid of pretty outputs says "it can make pictures"; so does every tool.
 * Showing the INPUTS beside the outputs — this jacket and this photo became this selfie and this
 * clip — is what says "it builds a pipeline", and the second row (the same workflow, new garment,
 * new model) is the reuse argument made with pictures instead of words.
 *
 * Each row is LandingPipelineRow, which the Templates page draws too.
 */

import { LandingPipelineRow } from './LandingPipelineRow'
import type { Pipeline } from './landing-content'

export function LandingPipelines({ pipelines }: { pipelines: Pipeline[] }): JSX.Element {
  return (
    <div className="lp-pipes">
      {pipelines.map((p) => (
        <LandingPipelineRow key={p.title} pipeline={p} />
      ))}
    </div>
  )
}

export default LandingPipelines

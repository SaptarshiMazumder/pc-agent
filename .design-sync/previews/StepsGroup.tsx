/* StepsGroup — a run of the agent's tool calls folded into one line: the live step while it
 * works, a check and the step count when done, a warning when any call failed. Click the header
 * to unfold every call. */
import { StepsGroup } from 'agent-app'

const tool = (name: string, result: string, extra: Record<string, unknown> = {}) => ({
  kind: 'tool' as const,
  id: name,
  name,
  args: {},
  result,
  done: true,
  isError: false,
  ...extra,
})

const Col = ({ children }: { children: React.ReactNode }) => (
  <div style={{ width: 640, padding: 16 }}>{children}</div>
)

export const Working = () => (
  <Col>
    <StepsGroup
      running
      items={[
        tool('kb_lookup', 'qwen-image: t2i-2-1-person-from-reference (tested)'),
        tool('stage_set', 'stage still: ports set (scene)'),
        tool('pipeline_run', '', { done: false, progress: 'Uploading reference_image.jpg\nRendering still — 38% (step 11/30)' }),
      ]}
    />
  </Col>
)

export const Done = () => (
  <Col>
    <StepsGroup
      running={false}
      items={[
        tool('kb_lookup', 'minimax-h3: r2v-ref2va-people-turbo8 (tested)'),
        tool('pipeline_validate', 'All 2 stages compile. No problems.'),
        tool('pipeline_run', 'stage video done: outputs/person.mp4 (124 frames, 5.2 s)'),
      ]}
    />
  </Col>
)

export const Failed = () => (
  <Col>
    <StepsGroup
      running={false}
      items={[
        tool('pipeline_validate', 'All 1 stages compile.'),
        tool('pipeline_run', 'Comfy Cloud: GPU out of memory on stage swap (VAEDecode)', { isError: true }),
      ]}
    />
  </Col>
)

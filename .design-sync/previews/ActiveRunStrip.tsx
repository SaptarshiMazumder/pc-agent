/* ActiveRunStrip — the run in flight as one line: which workflow, on which checkpoint, how long it
 * has been going, an estimated bar against the recent average, and Interrupt.
 *
 * `state` is the bridge's studio-state record (useStudioState, the `comfy_studio_state` poll).
 * Cells sweep the bar: an estimate when earlier runs finished (elapsed / their average duration),
 * indeterminate on a chat's first run (no average to compare against), and the strip with no
 * daemon connection (Interrupt disabled). With no active run the component renders NOTHING by
 * design — that state is an empty box, so it is not a cell. */
import { ActiveRunStrip } from 'agent-app'

const now = Math.floor(Date.now() / 1000)
const client = {
  request: async () => ({ text: 'Interrupted.' }),
  on: () => () => {},
  onStatus: () => () => {},
}

const RUNS = [
  { name: 'person_still.api.json', checkpoint: 'flux1-dev-fp8.safetensors', steps: 28, duration: 41, status: 'complete', outputs: 2, ts: now - 2400 },
  { name: 'talking_clip.api.json', checkpoint: 'wan2.2_s2v_14B_fp8.safetensors', steps: 20, duration: 118, status: 'complete', outputs: 1, ts: now - 1500 },
  { name: 'talking_clip.api.json', checkpoint: 'wan2.2_s2v_14B_fp8.safetensors', steps: 20, duration: 0, status: 'interrupted', outputs: 0, ts: now - 600 },
]

const ACTIVE = {
  workflow: 'talking_clip.api.json',
  prompt_id: '7c1f0d2e-5b4a-4e8b-9a61-3f2d8c0b7e19',
  checkpoint: 'wan2.2_s2v_14B_fp8.safetensors',
  steps: 20,
  started: now - 52,
  elapsed: 52,
  status: 'running',
}

const Strip = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 720 }}>{children}</div>

export const WithEstimate = () => (
  <Strip>
    <ActiveRunStrip client={client} state={{ active: ACTIVE, runs: RUNS }} />
  </Strip>
)

export const FirstRunOfTheChat = () => (
  <Strip>
    <ActiveRunStrip
      client={client}
      state={{
        active: { ...ACTIVE, workflow: 'person_still.api.json', checkpoint: 'flux1-dev-fp8.safetensors', steps: 28, elapsed: 14 },
        runs: [],
      }}
    />
  </Strip>
)

export const NoDaemonConnection = () => (
  <Strip>
    <ActiveRunStrip client={undefined} state={{ active: { ...ACTIVE, elapsed: 97 }, runs: RUNS }} />
  </Strip>
)

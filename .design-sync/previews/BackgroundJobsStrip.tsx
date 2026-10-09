/* BackgroundJobsStrip — the tool calls that left the turn and are still working, one row each,
 * above the composer: a pulsing dot, the tool, its own latest progress line (relayed from the
 * microVM, never an invented percentage), a local clock from the daemon's start stamp, and
 * Cancel for that one job. Renders nothing when there are no jobs (no empty cell).
 *
 * Cells: one long validate wait; several jobs (including one with no progress line yet, which
 * shows the component's own fallback); and a disconnected window (client null), where Cancel
 * is inert. The "stopping…" label follows a click and cannot be shown statically.
 * The daemon client is stubbed: `jobs.cancel` answers ok. */
import { BackgroundJobsStrip } from 'agent-app'

const client = {
  request: async (method: string) => (method === 'jobs.cancel' ? { ok: true } : {}),
  on: () => () => {},
  onStatus: () => () => {},
} as any

const SESSION = 'chat-1728391205-k3v9'
const ago = (s: number) => Date.now() - s * 1000

const VALIDATE = {
  id: 'j1',
  tool: 'comfy_validate',
  toolCallId: 'call_7f2a',
  startedAt: ago(252),
  text: 'waiting for ComfyUI-Manager to finish downloading (0/1 done) — wan2.2_s2v_14B_fp8.safetensors 9.4 of 16.1 GB',
}

export const OneJob = () => (
  <div style={{ maxWidth: 640 }}>
    <BackgroundJobsStrip jobs={[VALIDATE]} sessionKey={SESSION} client={client} />
  </div>
)

export const SeveralJobs = () => (
  <div style={{ maxWidth: 640 }}>
    <BackgroundJobsStrip
      jobs={[
        VALIDATE,
        {
          id: 'j2',
          tool: 'comfy_download',
          toolCallId: 'call_8c11',
          startedAt: ago(71),
          text: 'realism_skin_v2.safetensors — 142 of 228 MB from Civitai',
        },
        { id: 'j3', tool: 'comfy_install', toolCallId: 'call_9d40', startedAt: ago(8), text: '' },
      ]}
      sessionKey={SESSION}
      client={client}
    />
  </div>
)

export const Disconnected = () => (
  <div style={{ maxWidth: 640 }}>
    <BackgroundJobsStrip
      jobs={[{ ...VALIDATE, startedAt: ago(1340), text: 'waiting for ComfyUI-Manager to finish downloading (1/2 done)' }]}
      sessionKey={SESSION}
      client={null}
    />
  </div>
)

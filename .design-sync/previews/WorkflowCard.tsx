/* WorkflowCard — one ComfyUI workflow as a card with its two files: the run file
 * (`<name>.api.json`, what POST /prompt accepts) and the ComfyUI file (`<name>.json`, what the
 * editor imports), each a labelled download. The meta line sums both files' sizes and says
 * which of the two exist, plus whatever the screen appends (a Library version, its slots).
 *
 * Cells sweep the file axis (both / run only / import only) and the bookmark's save axis
 * (idle → saving → saved), with and without the optional Save and Delete buttons. Downloads
 * point at the daemon's /file endpoint; nothing is fetched until clicked. */
import { WorkflowCard } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'

const wfFile = (name: string, size: number) => ({
  path: `${WS}/workflows/${CHAT}/${name}`,
  name,
  mime: 'application/json',
  kind: 'file' as const,
  size,
})

const noop = () => {}

export const BothFiles = () => (
  <div style={{ maxWidth: 560 }}>
    <WorkflowCard
      wf={{
        name: 'flux_portrait_1024x1536',
        api: wfFile('flux_portrait_1024x1536.api.json', 14_820),
        ui: wfFile('flux_portrait_1024x1536.json', 38_410),
      }}
      onSave={noop}
      onDelete={noop}
    />
  </div>
)

export const LibraryVersion = () => (
  <div style={{ maxWidth: 560 }}>
    <WorkflowCard
      wf={{
        name: 'wan22_talking_portrait',
        api: wfFile('wan22_talking_portrait.api.json', 22_960),
        ui: wfFile('wan22_talking_portrait.json', 61_300),
      }}
      meta="v3 · slots: @face, @voice · from: Talking portrait for the launch"
      onDelete={noop}
    />
  </div>
)

export const RunFileOnlySaving = () => (
  <div style={{ maxWidth: 560 }}>
    <WorkflowCard
      wf={{ name: 'sdxl_product_on_marble', api: wfFile('sdxl_product_on_marble.api.json', 9_480) }}
      onSave={noop}
      saveState="saving"
    />
  </div>
)

export const ImportFileOnlySaved = () => (
  <div style={{ maxWidth: 560 }}>
    <WorkflowCard
      wf={{ name: 'ultimate_sd_upscale_4x', ui: wfFile('ultimate_sd_upscale_4x.json', 27_150) }}
      onSave={noop}
      saveState="saved"
    />
  </div>
)

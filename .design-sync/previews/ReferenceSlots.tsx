/* ReferenceSlots — the Inputs a workflow needs: one row per slot the agent declared (role, what it
 * asked for, the file filling it, Add file / Replace and the From Library door), extra files under
 * "Other", and an Upload that is always there.
 *
 * Slots are the merged `Slot` records (agentd/reference-slots.ts); files are this chat's Artifacts
 * at `references/<chat>/<role>.<ext>`. Cells sweep the slot axis: one input still waiting on the
 * person (warn-coloured count and dashed slot), every input filled plus the ones a pipeline step
 * feeds and a loose file under Other, nothing declared yet (the drop prompt), and no daemon
 * connection (buttons disabled). The drag-over highlight is a live drag state and is not shown. */
import { ReferenceSlots } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const ref = (name: string, kind: 'image' | 'audio', mime: string, size: number) => ({
  path: `${WS}/references/${CHAT}/${name}`,
  name,
  mime,
  kind,
  size,
  modified: now - 1200,
})

const FACE = ref('face.jpg', 'image', 'image/jpeg', 412_000)
const VOICE = ref('voice.wav', 'audio', 'audio/wav', 880_000)
const LOOSE = ref('IMG_4471.jpg', 'image', 'image/jpeg', 2_960_000)
const STILL = {
  path: `${WS}/outputs/${CHAT}/person_still_00002.png`,
  name: 'person_still_00002.png',
  mime: 'image/png',
  kind: 'image' as const,
  size: 2_280_000,
  modified: now - 900,
}

const noop = () => {}
const add = async () => {}
const Rail = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 520 }}>{children}</div>

export const OneInputWaiting = () => (
  <Rail>
    <ReferenceSlots
      slots={[
        { role: 'face', what: 'a clear, front-facing photo of the person', workflows: ['person_still'], file: FACE, fedBy: null },
        { role: 'voice', what: 'the line they say, a WAV or MP3 under 30 seconds', workflows: ['talking_clip'], file: null, fedBy: null },
      ]}
      free={[]}
      disabled={false}
      onAdd={add}
      onOpen={noop}
      onFromLibrary={noop}
    />
  </Rail>
)

export const AllFilledWithStepInputs = () => (
  <Rail>
    <ReferenceSlots
      slots={[
        { role: 'face', what: 'a clear, front-facing photo of the person', workflows: ['person_still'], file: FACE, fedBy: null },
        { role: 'voice', what: 'the line they say', workflows: ['talking_clip'], file: VOICE, fedBy: null },
        { role: 'person_still', what: '', workflows: ['talking_clip'], file: STILL, fedBy: 'person_still.image' },
        { role: 'end_frame', what: '', workflows: ['talking_clip'], file: null, fedBy: 'outro_frame.image' },
      ]}
      free={[LOOSE]}
      disabled={false}
      onAdd={add}
      onOpen={noop}
      onFromLibrary={noop}
    />
  </Rail>
)

export const NothingDeclaredYet = () => (
  <Rail>
    <ReferenceSlots slots={[]} free={[]} disabled={false} onAdd={add} onOpen={noop} onFromLibrary={noop} />
  </Rail>
)

export const NotConnected = () => (
  <Rail>
    <ReferenceSlots
      slots={[
        { role: 'product', what: 'the mug on a plain background, straight on', workflows: ['product_ad'], file: null, fedBy: null },
      ]}
      free={[]}
      disabled
      onAdd={add}
      onOpen={noop}
      onFromLibrary={noop}
    />
  </Rail>
)

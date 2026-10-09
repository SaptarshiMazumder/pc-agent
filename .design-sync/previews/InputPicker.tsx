/* InputPicker — the list a stage's input is chosen from, opened by Change on a StagePanel row.
 *
 *   fed by an earlier step   that step's results; choosing one picks it on that step
 *   filled by the person     every file of this chat of the input's kind, plus Upload / Library
 *   nothing yet              says which step has not made anything, or that no file fits
 *
 * Files are this chat's Artifacts (the real shape). Image tiles would draw the daemon's
 * `/thumbnail`; offline, OutputThumb falls back to its own placeholder glyph. */
import { InputPicker } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const file = (dir: string, name: string, kind: 'image' | 'video' | 'audio', mime: string, size: number) => ({
  path: `${WS}/${dir}/${CHAT}/${name}`,
  name,
  mime,
  kind,
  size,
  modified: now - 600,
})

const STILLS = [
  file('outputs', 'person_still_00001.png', 'image', 'image/png', 2_310_000),
  file('outputs', 'person_still_00002.png', 'image', 'image/png', 2_280_000),
  file('outputs', 'person_still_00003.png', 'image', 'image/png', 2_340_000),
]
const AUDIO = [
  file('references', 'voice.wav', 'audio', 'audio/wav', 880_000),
  file('outputs', 'voice_take2.wav', 'audio', 'audio/wav', 910_000),
]

const noop = () => {}
const Box = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 560 }}>{children}</div>

/** The talking clip's `person_still` input: the earlier step's three stills, the pick ringed. */
export const FromAnEarlierStep = () => (
  <Box>
    <InputPicker candidates={STILLS} current={STILLS[1]} fromStage="person_still" disabled={false} onChoose={noop} />
  </Box>
)

/** The person's own `voice` input: every audio file of the chat, plus Upload and the Library. */
export const PersonFills = () => (
  <Box>
    <InputPicker
      candidates={AUDIO}
      current={AUDIO[0]}
      fromStage=""
      disabled={false}
      onChoose={noop}
      onUpload={noop}
      onFromLibrary={noop}
    />
  </Box>
)

export const NothingMadeYet = () => (
  <Box>
    <InputPicker candidates={[]} current={null} fromStage="person_still" disabled={false} onChoose={noop} />
  </Box>
)

export const NoFileOfThisKind = () => (
  <Box>
    <InputPicker candidates={[]} current={null} fromStage="" disabled={false} onChoose={noop} onUpload={noop} onFromLibrary={noop} />
  </Box>
)

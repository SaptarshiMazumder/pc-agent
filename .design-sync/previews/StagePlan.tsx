/* StagePlan — the Workspace's Stages section: the chat's pipeline as steps the person drives.
 * Each step is a StagePanel (what it reads, what it will do, what it made, and its Run button).
 *
 * The plan is the real StagePlan record (stage_plan.json + pipeline_runs.json + stage_picks.json,
 * as useStagePlan merges them): two stages — `person_still` has run and made three stills (the
 * person picked the second), `talking_clip` has not run yet and is fed the picked still plus a
 * voice track the person added. Files are this chat's Artifacts at paths inside the chat's
 * folders (`outputs/<chat>/…`, `references/<chat>/…`), which is how results resolve to tiles.
 *
 * Result tiles are image Artifacts: the window would draw the daemon's `/thumbnail`; with no
 * daemon here OutputThumb falls back to its own placeholder glyph — the component's real
 * offline face, not a mock. */
import { StagePlan } from 'agent-app'

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

const FILES = [
  file('references', 'face.jpg', 'image', 'image/jpeg', 412_000),
  file('references', 'voice.wav', 'audio', 'audio/wav', 880_000),
  file('outputs', 'person_still_00001.png', 'image', 'image/png', 2_310_000),
  file('outputs', 'person_still_00002.png', 'image', 'image/png', 2_280_000),
  file('outputs', 'person_still_00003.png', 'image', 'image/png', 2_340_000),
]

const rel = (name: string) => `outputs/${CHAT}/${name}`

const PLAN = {
  name: 'talking_portrait',
  stages: [
    {
      name: 'person_still',
      facts: {
        model: 'flux1-dev-fp8',
        loras: ['realism_skin_v2'],
        size: '1024×1536',
        frames: null,
        fps: null,
        seconds: null,
        inputs: [{ role: 'face', from: 'you', output: '', frame: '', type: 'IMAGE' }],
        review: true,
        prompt:
          'Head-and-shoulders studio portrait of the person in the reference, soft key light from the left, warm grey seamless backdrop, natural skin texture, 85mm look.',
      },
    },
    {
      name: 'talking_clip',
      facts: {
        model: 'wan2.2_s2v_14B_fp8',
        loras: [],
        size: '720×1280',
        frames: 121,
        fps: 24,
        seconds: 5,
        inputs: [
          { role: 'person_still', from: 'person_still', output: 'image', frame: 'first', type: 'IMAGE' },
          { role: 'voice', from: 'you', output: '', frame: '', type: 'AUDIO' },
        ],
        review: false,
        prompt: 'She speaks to camera with small natural head movement; framing stays steady.',
      },
    },
  ],
  runs: {
    person_still: {
      status: 'done',
      results: [
        { at: now - 1800, outputs: { image: [rel('person_still_00001.png')] } },
        { at: now - 900, outputs: { image: [rel('person_still_00002.png'), rel('person_still_00003.png')] } },
      ],
      fed: { face: `references/${CHAT}/face.jpg` },
    },
  },
  picks: { person_still: rel('person_still_00002.png') },
}

const SLOTS = [
  { role: 'face', what: 'a clear, front-facing photo', workflows: ['person_still'], file: FILES[0], fedBy: null },
  { role: 'voice', what: 'the line they say', workflows: ['talking_clip'], file: FILES[1], fedBy: null },
]

const client = {
  request: async () => ({}),
  on: () => () => {},
  onStatus: () => () => {},
}
const noop = () => {}

const Panel = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 640 }}>{children}</div>

export const TwoStages = () => (
  <Panel>
    <StagePlan
      client={client}
      sessionKey={CHAT}
      plan={PLAN}
      files={FILES}
      slots={SLOTS}
      running={false}
      onAddReference={async () => {}}
      onFromLibrary={noop}
      onOpen={noop}
      onSend={noop}
    />
  </Panel>
)

export const WhileATurnRuns = () => (
  <Panel>
    <StagePlan
      client={client}
      sessionKey={CHAT}
      plan={PLAN}
      files={FILES}
      slots={SLOTS}
      running
      onAddReference={async () => {}}
      onFromLibrary={noop}
      onOpen={noop}
      onSend={noop}
    />
  </Panel>
)

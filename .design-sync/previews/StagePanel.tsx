/* StagePanel — one stage of the chat's pipeline, as StagePlan lists them:
 *
 *   head     step number, name, model, where it stands, and Run (the person's approval)
 *   facts    size · length · LoRAs · "you see it before the next step" — read off the graph
 *   inputs   each input with the file it will be given, its source, and Change
 *   results  every file the stage made; the picked one (what the next step gets) is ringed
 *   prompt   folded to two lines
 *
 * Fixture: the real StagePlan record shape (stage-plan.ts) for a two-stage talking-portrait
 * pipeline. Result tiles are image Artifacts; without a daemon `/thumbnail` OutputThumb draws its
 * own placeholder glyph. The input picker opens on a click (internal state) — see InputPicker. */
import { StagePanel } from 'agent-app'

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

const noop = () => {}
const byRelMap = new Map(FILES.map((f) => [f.path.slice(WS.length + 1), f]))
const byRel = (r: string) => (r ? byRelMap.get(r) || null : null)
const chatMedia = FILES

const panel = (index: number, plan: typeof PLAN, slots: typeof SLOTS, busy = false) => (
  <div className="sp-list" style={{ maxWidth: 640 }}>
    <StagePanel
      index={index}
      stage={plan.stages[index]}
      plan={plan}
      byRel={byRel}
      chatMedia={chatMedia}
      slots={slots}
      busy={busy}
      onRun={noop}
      onPick={noop}
      onSetInput={noop}
      onUpload={noop}
      onFromLibrary={noop}
      onOpen={noop}
    />
  </div>
)

/** Stage 1 has run twice and made three stills; the person picked the second (ringed). */
export const RanWithResults = () => panel(0, PLAN, SLOTS)

/** Stage 2 is fed the picked still (opens on it) plus the person's voice track; not run yet. */
export const ReadyToRun = () => panel(1, PLAN, SLOTS)

/** The person has not added their voice track: the input says so and Run waits. */
export const MissingInput = () => panel(1, PLAN, [SLOTS[0], { ...SLOTS[1], file: null }])

export const Rendering = () =>
  panel(1, { ...PLAN, runs: { ...PLAN.runs, talking_clip: { status: 'rendering', results: [], fed: {} } } }, SLOTS)

export const Failed = () =>
  panel(
    1,
    {
      ...PLAN,
      runs: {
        ...PLAN.runs,
        talking_clip: { status: 'failed', why: 'GPU out of memory at VAEDecode', results: [], fed: {} },
      },
    },
    SLOTS,
  )

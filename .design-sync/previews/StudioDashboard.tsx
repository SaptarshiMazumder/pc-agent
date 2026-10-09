/* StudioDashboard — the stage beside the conversation: the top bar (Workspace / Library tabs,
 * Comfy Cloud, credits), the run-in-flight strip, and the Workspace's one scroll of sections —
 * Your keys, Inputs (ReferenceSlots), Stages (StagePlan), Outputs (OutputsGrid), Workflow
 * (WorkflowPanel) and All files (FileExplorer, folded).
 *
 * Cells sweep the chat's lifecycle: a talking-portrait chat mid-run (two stages, the first with
 * three stills and a pick, its workflows and installer emitted, the clip rendering), a chat whose
 * agent has asked for a photo that is not added yet (the Workspace tab and Inputs flagged), and a
 * fresh chat ("Nothing made yet").
 *
 * HOW THE DATA ARRIVES, honestly: the daemon client is stubbed per method — `config.get` answers
 * the agent's two declared keys (one saved), `workspace.list` lists this chat's stage records.
 * The Stages section reads those records over the daemon's `/file` endpoint with `fetch`, which
 * has no server here, so this module answers exactly those three URLs (stage_plan.json,
 * pipeline_runs.json, stage_picks.json for this chat) with the StagePlan fixture and passes every
 * other request through untouched. Image tiles still draw OutputThumb's offline placeholder glyph
 * (no `/thumbnail`). Not reachable statically: the Library tab and the file viewer pane (both open
 * on a click), and Interrupt / Save / Delete outcomes. */
import { StudioDashboard } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WAITING_CHAT = 'chat-1728402911-m7qa'
const FRESH_CHAT = 'chat-1728410377-p2xd'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const file = (
  chat: string,
  dir: string,
  name: string,
  kind: 'image' | 'audio' | 'file',
  mime: string,
  size: number,
  ago: number,
) => ({
  path: `${WS}/${dir}/${chat}/${name}`,
  name,
  mime,
  kind,
  size,
  modified: now - ago,
})

/* ── the running chat ─────────────────────────────────────────────────────────────────────── */

const FACE = file(CHAT, 'references', 'face.jpg', 'image', 'image/jpeg', 412_000, 2400)
const VOICE = file(CHAT, 'references', 'voice.wav', 'audio', 'audio/wav', 880_000, 2300)
const STILLS = [
  file(CHAT, 'outputs', 'person_still_00001.png', 'image', 'image/png', 2_310_000, 1800),
  file(CHAT, 'outputs', 'person_still_00002.png', 'image', 'image/png', 2_280_000, 900),
  file(CHAT, 'outputs', 'person_still_00003.png', 'image', 'image/png', 2_340_000, 900),
]
const WORKFLOW_FILES = [
  file(CHAT, 'workflows', 'person_still.api.json', 'file', 'application/json', 6_412, 2100),
  file(CHAT, 'workflows', 'person_still.json', 'file', 'application/json', 14_906, 2100),
  file(CHAT, 'workflows', 'install_person_still.py', 'file', 'text/x-python', 9_870, 1900),
  file(CHAT, 'workflows', 'install_person_still.manifest.json', 'file', 'application/json', 2_214, 1900),
  file(CHAT, 'workflows', 'talking_clip.api.json', 'file', 'application/json', 9_133, 1300),
  file(CHAT, 'workflows', 'talking_clip.json', 'file', 'application/json', 21_480, 1300),
]
const ARTIFACTS = [FACE, VOICE, ...WORKFLOW_FILES, ...STILLS]

const SLOTS = [
  { role: 'face', what: 'a clear, front-facing photo', workflows: ['person_still'], file: FACE, fedBy: null },
  { role: 'voice', what: 'the line they say', workflows: ['talking_clip'], file: VOICE, fedBy: null },
]

const rel = (name: string) => `outputs/${CHAT}/${name}`

/* The three stage records exactly as the plugin and this window write them (agentd/stage-plan.ts). */
const STAGE_PLAN_JSON = {
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
}
const PIPELINE_RUNS_JSON = {
  person_still: {
    status: 'done',
    results: [
      { at: now - 1800, outputs: { image: [rel('person_still_00001.png')] } },
      { at: now - 900, outputs: { image: [rel('person_still_00002.png'), rel('person_still_00003.png')] } },
    ],
    fed: { face: `references/${CHAT}/face.jpg` },
  },
}
const STAGE_PICKS_JSON = { person_still: rel('person_still_00002.png') }

const STAGES_DIR = `workflows/${CHAT}/stages`
const RECORDS: Record<string, unknown> = {
  [`${WS}/${STAGES_DIR}/stage_plan.json`]: STAGE_PLAN_JSON,
  [`${WS}/${STAGES_DIR}/pipeline_runs.json`]: PIPELINE_RUNS_JSON,
  [`${WS}/${STAGES_DIR}/stage_picks.json`]: STAGE_PICKS_JSON,
}

/* Answers ONLY this chat's three stage-record URLs on the daemon's `/file` endpoint; everything
   else goes to the real fetch. */
if (typeof window !== 'undefined' && !(window as { __studioDashboardFetch?: boolean }).__studioDashboardFetch) {
  ;(window as { __studioDashboardFetch?: boolean }).__studioDashboardFetch = true
  const realFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const href = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(href, location.href)
    const path = url.pathname.endsWith('/file') ? url.searchParams.get('path') : null
    if (path && path in RECORDS) {
      return new Response(JSON.stringify(RECORDS[path]), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return realFetch(input, init)
  }
}

const RUNS = [
  { name: 'person_still.api.json', checkpoint: 'flux1-dev-fp8.safetensors', steps: 28, duration: 41, status: 'complete', outputs: 1, ts: now - 1800 },
  { name: 'person_still.api.json', checkpoint: 'flux1-dev-fp8.safetensors', steps: 28, duration: 77, status: 'complete', outputs: 2, ts: now - 900 },
  { name: 'talking_clip.api.json', checkpoint: 'wan2.2_s2v_14B_fp8.safetensors', steps: 20, duration: 124, status: 'complete', outputs: 1, ts: now - 3600 * 26 },
]
const STUDIO_RUNNING = {
  active: {
    workflow: 'talking_clip.api.json',
    prompt_id: '7c1f0d2e-5b4a-4e8b-9a61-3f2d8c0b7e19',
    checkpoint: 'wan2.2_s2v_14B_fp8.safetensors',
    steps: 20,
    started: now - 38,
    elapsed: 38,
    status: 'running',
  },
  runs: RUNS,
}

/* ── the daemon, per method ───────────────────────────────────────────────────────────────── */

const CONFIG = {
  settings: [
    {
      key: 'USER_COMFY_API_KEY',
      label: 'Your Comfy API key',
      help: 'Runs every workflow on Comfy Cloud with your Comfy plan (Standard or above; importing models needs Creator). Get it at platform.comfy.org → API keys.',
    },
    {
      key: 'USER_CIVITAI_TOKEN',
      label: 'Your Civitai API key',
      help: 'Lets Comfy Penguin import Civitai models and LoRAs that need a login. Civitai → Account settings → API keys.',
    },
  ],
  env: { USER_COMFY_API_KEY: true, USER_CIVITAI_TOKEN: false },
}

const client = {
  request: async (method: string, params?: { path?: string }) => {
    if (method === 'config.get') return CONFIG
    if (method === 'workspace.list') {
      if (params?.path === STAGES_DIR) {
        return {
          entries: ['stage_plan.json', 'pipeline_runs.json', 'stage_picks.json'].map((name) => ({
            name,
            path: `${WS}/${STAGES_DIR}/${name}`,
          })),
        }
      }
      return { entries: [] }
    }
    return {}
  },
  on: () => () => {},
  onStatus: () => () => {},
}

const noop = () => {}
const Stage = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 760 }}>{children}</div>

/* The thread is what the Design phase reads (agentd/creation-phase.ts): a user message after
   the design card means it is answered, so the stages show; no plan and no card means the agent
   is still designing. */
const ASK_CARD = {
  kind: 'tool' as const,
  id: 'call_present_1',
  name: 'pipeline_present',
  args: {
    title: 'Talking portrait — 2 steps',
    workflows: [
      { name: 'person_still', does: 'a studio portrait of the person in the photo' },
      { name: 'talking_clip', does: 'the portrait says the line, 8 s' },
    ],
    delivers: 'one 8-second 1080×1920 clip',
  },
  done: true,
  isError: false,
  result: '',
  ts: now * 1000 - 3_000_000,
}
const ANSWERED_THREAD = [
  { kind: 'user' as const, text: 'Make my photo say this line.', ts: now * 1000 - 3_600_000 },
  ASK_CARD,
  { kind: 'user' as const, text: 'Build it as proposed.', ts: now * 1000 - 2_900_000 },
] as never[]
const WAITING_THREAD = [{ kind: 'user' as const, text: 'Put my logo on the mug.', ts: now * 1000 - 600_000 }] as never[]

const common = {
  client,
  onAddReference: async () => {},
  referencesDisabled: false,
  onCredits: noop,
  onDeleteFiles: async () => {},
  onAddToLibrary: (async () => ({ ok: true, message: 'Added to your Library.' })) as never,
  workspaceVersion: 1,
  onRunAgain: noop,
  onOpenLibrary: noop,
  onFromLibrary: noop,
  onSend: noop,
  onDecide: noop,
  workflowName: 'talking_clip.api.json',
}

export const MidRunWithStages = () => (
  <Stage>
    <StudioDashboard
      {...common}
      sessionKey={CHAT}
      items={ANSWERED_THREAD}
      empty={false}
      title="Talking portrait"
      state={STUDIO_RUNNING}
      running
      artifacts={ARTIFACTS}
      slots={SLOTS}
      freeReferences={[]}
      credits={12400}
      onSaveTemplate={noop}
      deletionDisabled="Wait for the current turn to finish"
    />
  </Stage>
)

const PRODUCT_LOGO = file(WAITING_CHAT, 'references', 'logo.png', 'image', 'image/png', 96_000, 300)

export const WaitingOnAPhoto = () => (
  <Stage>
    <StudioDashboard
      {...common}
      sessionKey={WAITING_CHAT}
      items={WAITING_THREAD}
      empty={false}
      title="Logo on the mug"
      workflowName=""
      state={{ runs: RUNS }}
      running
      artifacts={[PRODUCT_LOGO]}
      slots={[
        { role: 'product', what: 'the mug on a plain background, shot straight on', workflows: [], file: null, fedBy: null },
        { role: 'logo', what: 'your logo, transparent PNG if you have one', workflows: [], file: PRODUCT_LOGO, fedBy: null },
      ]}
      freeReferences={[]}
      credits={12400}
    />
  </Stage>
)

export const FreshChat = () => (
  <Stage>
    <StudioDashboard
      {...common}
      sessionKey={FRESH_CHAT}
      items={[]}
      empty
      title="New creation"
      workflowName=""
      state={{}}
      running={false}
      artifacts={[]}
      slots={[]}
      freeReferences={[]}
      credits={12400}
    />
  </Stage>
)

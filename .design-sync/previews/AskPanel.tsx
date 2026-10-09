/* AskPanel — the agent's question to the person, rendered from a tool call's arguments (never
 * parsed out of prose). Two shapes:
 *
 *   pipeline card   `pipeline_present`'s checkpoint `ask`: its workflows carry `facts` (the stage's
 *                   model, inputs, size, length, LoRAs — read off the graph), so the card is drawn
 *                   as short factual lines with a "N steps · delivers …" head.
 *   ask_user        services to tick (grouped by `step`: one pick per step, nothing pre-ticked),
 *                   brief questions prefilled with the agent's defaults, and the build order.
 *
 * In the window MessageItem renders it under the tool row; `answered` flips once a user message
 * follows the call. The item is the real ToolItem shape (`done: true`, `isError: false`). Each cell keeps
 * to one concern so it fits a card: models-per-step, the brief, the pipeline card. */
import { AskPanel } from 'agent-app'

const noop = () => {}

const PIPELINE = {
  kind: 'tool' as const,
  id: 'call_pp_01',
  name: 'pipeline_present',
  args: {},
  result: 'Pipeline ready for approval.',
  done: true,
  isError: false,
  ask: {
    title: 'Talking portrait from your photo',
    delivers: 'a 5 s vertical clip',
    results: '2 stills to pick from, then the clip',
    workflows: [
      {
        name: 'person_still',
        does: 'a studio portrait of the person in your photo',
        facts: {
          model: 'flux1-dev-fp8',
          loras: ['realism_skin_v2'],
          size: '1024×1536',
          frames: null,
          fps: null,
          seconds: null,
          inputs: [{ role: 'face', from: 'you', output: '', frame: '', type: 'IMAGE' }],
          review: true,
          prompt: 'Head-and-shoulders portrait, soft key light from the left, warm grey backdrop.',
        },
      },
      {
        name: 'talking_clip',
        does: 'animates the picked still to your voice track',
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
          prompt: 'She speaks to camera, small natural head movement, steady framing.',
        },
      },
    ],
    references: [
      { role: 'face', what: 'a clear, front-facing photo of the person' },
      { role: 'voice', what: 'the line they say, 5 s or less' },
    ],
  },
}

const MODELS = {
  kind: 'tool' as const,
  id: 'call_ask_02',
  name: 'ask_user',
  args: {
    title: 'Product ad — ceramic mug on linen',
    services: [
      { name: 'Seedream 4', purpose: 'Hero still, sharp product detail', credits: 60, step: 'hero_still' },
      { name: 'Flux Kontext Pro', purpose: 'Keeps the mug label exact', credits: 80, step: 'hero_still' },
      { name: 'Kling 2.1 Master', purpose: '5 s slow push-in', credits: 560, step: 'push_in' },
      { name: 'Seedance 1 Pro', purpose: '5 s, cheaper, softer motion', credits: 300, step: 'push_in' },
      { name: 'ElevenLabs SFX', purpose: 'Pour and steam sound bed', credits: 40, step: '' },
    ],
    workflows: [
      { name: 'hero_still', does: 'the product still' },
      { name: 'push_in', does: 'the slow camera push-in' },
    ],
  },
  result: '',
  done: true,
  isError: false,
}

const BRIEF = {
  kind: 'tool' as const,
  id: 'call_ask_03',
  name: 'ask_user',
  args: {
    title: 'Anime key visual, rainy neon street',
    questions: [
      { question: 'Size', default: '1536×864' },
      { question: 'Checkpoint', default: 'animagine-xl-4.0' },
    ],
    workflows: [{ name: 'anime_kv.api.json', does: 'text-to-image with a hires-fix pass' }],
    imports: 'Runs on your instance — no credits',
  },
  result: '',
  done: true,
  isError: false,
}

const Column = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 620 }}>{children}</div>

export const PipelineCard = () => (
  <Column>
    <AskPanel item={PIPELINE} answered={false} onDecide={noop} />
  </Column>
)

export const PickAModelPerStep = () => (
  <Column>
    <AskPanel item={MODELS} answered={false} onDecide={noop} />
  </Column>
)

export const BriefDefaults = () => (
  <Column>
    <AskPanel item={BRIEF} answered={false} onDecide={noop} />
  </Column>
)

export const Answered = () => (
  <Column>
    <AskPanel item={BRIEF} answered onDecide={noop} />
  </Column>
)

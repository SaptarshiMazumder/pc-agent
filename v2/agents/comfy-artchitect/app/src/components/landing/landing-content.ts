/* The landing page's words and pictures, as data.
 *
 * SEPARATE FROM THE LAYOUT so the pitch can be rewritten without touching a component, and so the
 * sections stay dumb: each one takes its list and draws it.
 *
 * THE PITCH — what a visitor must understand in five seconds:
 *   An AI agent that makes images and videos with the best model for each step, free or premium,
 *   and saves the whole job as a workflow you reuse forever — here or in your own ComfyUI.
 *   Built for India: free open-source models first, premium only where it wins, pay as you go in
 *   rupees. Cheaper and simpler than subscription apps.
 *
 * LESS TEXT, ON PURPOSE. One headline and at most one short line per section; the pictures carry
 * the proof. A sentence that needs a second read is cut, not reworded.
 *
 * EVERY PICTURE IS A REAL RENDER in public/marketing/, and every pipeline shown is a real one:
 * those inputs went in, those outputs came out. A missing file degrades to a labelled frame
 * (LandingShot), never a broken image.
 *
 * MODEL NAMES ARE ONLY ONES THE AGENT REACHES — the partner nodes and open models in the
 * comfyui-workflows skill's field guide. The pipelines do NOT say which model made them, because
 * the page cannot prove it. Competitors are never named: the comparison is with "other AI video
 * apps" in general, on facts that hold for the category (dollar subscriptions, one-off results).
 */

export type MediaKind = 'image' | 'video'

export type ReelShot = { src: string; alt: string; kind: MediaKind; width: number; label?: string }

/** The proof reel under the hero: mixed stills and clips, at their own widths. */
export const RENDER_REEL: ReelShot[] = [
  { src: 'marketing/tryon-jacket-post.webp', alt: 'Mirror selfie in a souvenir jacket', kind: 'image', width: 150 },
  { src: 'marketing/style-shiba-samurai.webp', alt: 'Shiba samurai in ink-wash style', kind: 'image', width: 260 },
  { src: 'marketing/tryon-kimono-reel.mp4', alt: 'Talking reel in a kimono', kind: 'video', width: 150, label: '00:03' },
  { src: 'marketing/street-red-car.webp', alt: 'Street portrait by a red car', kind: 'image', width: 260 },
  { src: 'marketing/style-cat-boat.webp', alt: 'Cat in a boat, ukiyo-e', kind: 'image', width: 458 },
  { src: 'marketing/selfie-street-post.webp', alt: 'Street post from a selfie', kind: 'image', width: 150 },
  { src: 'marketing/upscale-lavender-reader.webp', alt: 'Reading in a lavender field', kind: 'image', width: 260 },
  { src: 'marketing/tryon-jacket-reel.mp4', alt: 'Talking street reel in a souvenir jacket', kind: 'video', width: 150, label: '00:04' },
  { src: 'marketing/pet-mainecoon-after.webp', alt: 'Maine coon on a back-seat ride', kind: 'image', width: 260 },
  { src: 'marketing/night-kissaten.webp', alt: 'Night café scene', kind: 'image', width: 260 },
  { src: 'marketing/style-shiba-blossom.webp', alt: 'Shiba under cherry blossom', kind: 'image', width: 458 },
]

/** The three reasons, right under the hero — the whole pitch in three cards. */
export const VALUE_POINTS: { title: string; body: string }[] = [
  { title: 'Just describe it', body: 'No prompts to engineer, no tools to learn. Penguin plans, builds and fixes it.' },
  { title: 'Free models first', body: 'Open-source where they’re as good. Premium only where it’s worth it.' },
  { title: 'Reuse it forever', body: 'Every result is a saved workflow — run it again here, or in your own ComfyUI.' },
]

/** The three beats — the same loop the product runs. */
export const LOOP_STEPS = [
  { n: '01', title: 'Describe it', body: 'Plain words. Add a photo if you have one.' },
  { n: '02', title: 'Pick the plan', body: 'Choose premium or free models. See the credits before anything runs.' },
  { n: '03', title: 'Get it — and keep it', body: 'Your images and videos, plus the workflow to run again.' },
]

/** A real pipeline, shown end to end: what went in, and each thing that came out of it. */
export type PipelineStep = { src: string; label: string; kind: MediaKind | 'input' }
export type Pipeline = { title: string; prompt: string; inputs: PipelineStep[]; outputs: PipelineStep[] }

export const PIPELINES: Pipeline[] = [
  {
    title: 'Try-on → talking reel',
    prompt: 'Put this jacket on her, then make a talking clip for Reels.',
    inputs: [
      { src: 'marketing/tryon-jacket-garment.webp', label: 'Jacket', kind: 'input' },
      { src: 'marketing/tryon-jacket-model.webp', label: 'Model', kind: 'input' },
    ],
    outputs: [
      { src: 'marketing/tryon-jacket-post.webp', label: 'Selfie', kind: 'image' },
      { src: 'marketing/tryon-jacket-reel.mp4', label: 'Reel', kind: 'video' },
    ],
  },
  {
    title: 'Same workflow, new outfit',
    prompt: 'Run it again with this kimono.',
    inputs: [
      { src: 'marketing/tryon-kimono-garment.webp', label: 'Kimono', kind: 'input' },
      { src: 'marketing/tryon-kimono-model.webp', label: 'Model', kind: 'input' },
    ],
    outputs: [
      { src: 'marketing/tryon-kimono-post.webp', label: 'Selfie', kind: 'image' },
      { src: 'marketing/tryon-kimono-reel.mp4', label: 'Reel', kind: 'video' },
    ],
  },
  {
    title: 'One selfie → an influencer',
    prompt: 'Make her a street post, then a clip of her talking.',
    inputs: [{ src: 'marketing/selfie-face.webp', label: 'Selfie', kind: 'input' }],
    outputs: [
      { src: 'marketing/selfie-street-post.webp', label: 'Post', kind: 'image' },
      { src: 'marketing/talking-street-reel.mp4', label: 'Reel', kind: 'video' },
    ],
  },
]

/* ── premium where it matters, free where it does not ─────────────────────────────────────── */

/** An EXAMPLE of how one workflow mixes the two: a real job (one product photo in, an ad reel
 *  out) with the model each step would use. Every workflow gets the same step-by-step choice;
 *  this is not a claim about which model made the renders on this page. */
export const MIX_TITLE = 'Example · one jacket photo → a vertical ad reel'
export const MIX_EXAMPLE: { step: string; model: string; tier: 'open' | 'premium' }[] = [
  { step: 'Put the jacket on a model', model: 'Qwen-Image-Edit', tier: 'open' },
  { step: 'Shoot 3 angles, same face', model: 'FLUX.2 klein', tier: 'open' },
  { step: '8 s walk-and-turn clip', model: 'Kling 3.0', tier: 'premium' },
  { step: 'Upscale to 4K', model: 'SeedVR2', tier: 'open' },
]

export const MODEL_TIERS: {
  tier: 'premium' | 'open'
  title: string
  sub: string
  groups: { kind: MediaKind; names: string[] }[]
}[] = [
  {
    tier: 'open',
    title: 'Open source',
    sub: 'Free models — no credits used.',
    groups: [
      { kind: 'video', names: ['Wan 2.2', 'LTX-2.5', 'MiniMax H3 (open)', 'SeedVR2'] },
      { kind: 'image', names: ['Qwen-Image', 'Qwen-Image-Edit', 'FLUX.2 klein', 'Z-Image Turbo', 'HiDream-O1', 'Krea 2', 'Real-ESRGAN'] },
    ],
  },
  {
    tier: 'premium',
    title: 'Premium',
    sub: 'Top paid models, in credits. No accounts, no API keys.',
    groups: [
      { kind: 'video', names: ['Kling 3.0', 'Veo 3.1', 'Seedance 2.5', 'Wan 3.0', 'MiniMax H3 Max', 'HeyGen'] },
      { kind: 'image', names: ['Nano Banana Pro', 'GPT Image 2', 'Seedream 5.0', 'FLUX.2 Pro', 'Recraft V4', 'Ideogram 4', 'Topaz'] },
    ],
  },
]

/* ── one setup, endless runs: one pet-portrait workflow, four pets, a few styles ──────────── */

export const STYLE_RUNS: { before: string; after: string; name: string; style: string }[] = [
  { before: 'marketing/pet-poodle-before.webp', after: 'marketing/pet-poodle-after.webp', name: 'Poodle', style: 'ink wash' },
  { before: 'marketing/pet-blackshiba-before.webp', after: 'marketing/pet-blackshiba-after.webp', name: 'Shiba', style: 'ukiyo-e' },
  { before: 'marketing/pet-tabby-before.webp', after: 'marketing/pet-tabby-after.webp', name: 'Tabby', style: 'samurai' },
  { before: 'marketing/pet-mainecoon-before.webp', after: 'marketing/pet-mainecoon-after.webp', name: 'Maine coon', style: 'oil paint' },
]

/* ── Comfy Penguin vs other AI video apps ───────────────────────────────────────────────── */

/** [what, other apps, Comfy Penguin]. Short cells: a row that needs a second read is cut. */
export const COMPARE_ROWS: [string, string, string][] = [
  ['Pricing', 'Monthly subscription, in dollars', 'Pay as you go, in rupees'],
  ['Models', 'Premium models for every step', 'Free open-source first, premium where it wins'],
  ['What you get', 'One-off images and videos', 'The result and a reusable workflow'],
  ['Getting started', 'Learn their tools, templates and prompts', 'Describe it in plain words'],
  ['Your work', 'Stays on their platform', 'Export it to your own ComfyUI'],
]

/* ── pricing ───────────────────────────────────────────────────────────────────────────── */

/** The credit packs, as the accounts service sells them.
 *
 *  THE SOURCE OF TRUTH IS THE ACCOUNTS CATALOGUE (the `products` table the Credits page reads
 *  after sign-in, paid through Razorpay in INR). These mirror what that page sells today and must
 *  be updated here if the catalogue changes. `workflows` is a rough guide to what a pack buys —
 *  a workflow's cost depends on its models. */
export const CREDIT_PACKS: { name: string; credits: number; inr: string; workflows: string; note?: string }[] = [
  { name: 'Starter', credits: 50_000, inr: '₹89', workflows: '~1–2 workflows' },
  { name: 'Basic', credits: 250_000, inr: '₹445', workflows: '~3–6 workflows' },
  { name: 'Plus', credits: 500_000, inr: '₹890', workflows: '~10–20 workflows', note: 'Most popular' },
  { name: 'Pro', credits: 1_000_000, inr: '₹1,780', workflows: '~20–50 workflows' },
  { name: 'Studio', credits: 2_500_000, inr: '₹4,450', workflows: '~50–100 workflows' },
  { name: 'Max', credits: 5_000_000, inr: '₹8,900', workflows: '~100–500 workflows' },
]
export const SIGNUP_CREDITS = 10_000
export const SIGNUP_WORKFLOWS = '~1 workflow'
export const PACK_PERKS = ['Every model, free and premium', 'Valid for 365 days']

export type StarterWorkflow = {
  title: string
  steps: [string, string][] // [bold input, rest] pairs, rendered as one sentence
  kind: MediaKind
  shots: [string, string, string]
}

/** Ready-made starting points. On this page every one opens sign-in. */
export const STARTER_WORKFLOWS: StarterWorkflow[] = [
  {
    title: 'Virtual try-on',
    steps: [['Garment', ' + '], ['model', ' → on-model photo']],
    kind: 'image',
    shots: ['marketing/tryon-jacket-post.webp', 'marketing/tryon-jacket-garment.webp', 'marketing/tryon-jacket-model.webp'],
  },
  {
    title: 'Try-on to talking reel',
    steps: [['Garment', ' + '], ['model', ' → talking clip']],
    kind: 'video',
    shots: ['marketing/tryon-kimono-reel-poster.jpg', 'marketing/tryon-kimono-garment.webp', 'marketing/tryon-kimono-post.webp'],
  },
  {
    title: 'AI influencer',
    steps: [['One selfie', ' → posts and reels, same face']],
    kind: 'video',
    shots: ['marketing/talking-street-reel-poster.jpg', 'marketing/selfie-face.webp', 'marketing/selfie-street-post.webp'],
  },
  {
    title: 'Pet portrait, any style',
    steps: [['Pet photo', ' + '], ['a style', ' → painting']],
    kind: 'image',
    shots: ['marketing/pet-blackshiba-after.webp', 'marketing/pet-poodle-scholar.webp', 'marketing/pet-mainecoon-after.webp'],
  },
  {
    title: 'Street-style shoot',
    steps: [['A face', ' → a full photoshoot']],
    kind: 'image',
    shots: ['marketing/street-red-car.webp', 'marketing/street-red-car-take2.webp', 'marketing/street-black-coupe.webp'],
  },
  {
    title: 'Upscale and restore',
    steps: [['Any image', ' → sharp 2.5K']],
    kind: 'image',
    shots: ['marketing/upscale-lavender-reader.webp', 'marketing/upscale-champagne-lounge.webp', 'marketing/upscale-forest-laugh.webp'],
  },
]

/* LibraryTemplateCard — a template in the Library: a whole reusable setup (every workflow,
 * installer and input of a chat) kept as one. The card reads the template's own template.json,
 * so its meta line says how many workflows it holds, which inputs it needs (@face, @voice) and
 * where it came from; then Use this template, Download, About (when the template carries one)
 * and a Workflows toggle that unfolds its steps.
 *
 * Fixture: the card lists its folder through agentd/library-template.ts (`workspace.list` on
 * `library/<path>` and `library/<path>/workflows`, stubbed on the client) and fetches
 * template.json from the daemon's /file endpoint — this page's fetch is stubbed for exactly
 * those manifest paths (anything else passes through). No fixture carries a thumbnail: the
 * daemon's /thumbnail can't answer offline and the card has no image fallback, so the cards draw
 * the blank-thumb glyph, the card's own face for a template saved without one.
 * Cells sweep: a saved multi-step template with inputs + about, an uploaded single-step one,
 * and a template whose folder lost its template.json (the card's error line, Use disabled).
 * The unfolded Workflows list and the About panel open on a click only — not shown. */
import { LibraryTemplateCard } from 'agent-app'

const WS = '/srv/agentd/workspace'

const tplItem = (name: string, origin: 'saved' | 'uploaded', from?: string) => ({
  id: `tpl_${name.replace(/[^a-z0-9]/g, '').slice(0, 12)}`,
  kind: 'template' as const,
  origin,
  name,
  note: '',
  path: `${origin}/templates/${name}`,
  versions: [],
  ...(from ? { from: { chat: 'chat-1728391205-k3v9', title: from } } : {}),
  created: '2026-10-04T16:02:00.000Z',
})

const TALKING = tplItem('talking-portrait-launch', 'saved', 'Talking portrait for the launch')
const POSTER = tplItem('qwen-poster-text', 'uploaded')
const BROKEN = tplItem('sdxl-product-shots', 'saved', 'Sneaker drop product shots')

const MANIFESTS: Record<string, unknown> = {
  [`${WS}/library/${TALKING.path}/template.json`]: {
    format: 'comfy-penguin-template',
    version: 2,
    name: 'talking-portrait-launch',
    description:
      'A 1024×1536 Flux portrait of your face, animated into a 5-second Wan 2.2 clip that lip-syncs to your voice recording.',
    created: '2026-10-04T16:02:00.000Z',
    about: 'about.json',
    inputs: [
      { role: 'face', what: 'a front-facing photo, even light' },
      { role: 'voice', what: 'a 5–10 s voice recording' },
    ],
    steps: [
      {
        role: 'person_still',
        api: 'workflows/person_still.api.json',
        ui: 'workflows/person_still.json',
        installer: ['workflows/install_person_still.py'],
        slots: ['face'],
      },
      {
        role: 'talking_clip',
        api: 'workflows/talking_clip.api.json',
        ui: 'workflows/talking_clip.json',
        installer: [],
        slots: ['voice'],
      },
      { role: 'upscale', api: 'workflows/upscale.api.json', installer: [], slots: [] },
    ],
  },
  [`${WS}/library/${POSTER.path}/template.json`]: {
    format: 'comfy-penguin-template',
    version: 1,
    name: 'qwen-poster-text',
    description: 'Qwen-Image poster with your product and legible headline text, 1328×1328.',
    created: '2026-09-28T10:11:00.000Z',
    inputs: [{ role: 'product', what: 'the product on a plain background' }],
    steps: [{ role: 'poster', api: 'workflows/poster.api.json', ui: 'workflows/poster.json', installer: [], slots: ['product'] }],
  },
}

// The daemon's /file endpoint, for these manifest paths only.
const realFetch = window.fetch.bind(window)
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const m = /[?&]path=([^&]+)/.exec(url)
  const path = m ? decodeURIComponent(m[1]) : ''
  if (path in MANIFESTS) {
    return new Response(JSON.stringify(MANIFESTS[path]), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  return realFetch(input, init)
}) as typeof fetch

const entry = (dir: string, name: string, size: number) => ({ name, kind: 'file', size, path: `${WS}/${dir}/${name}` })

/** Each template folder's listing, keyed by its workspace-relative path. */
const LISTINGS: Record<string, unknown[]> = {
  [`library/${TALKING.path}`]: [
    entry(`library/${TALKING.path}`, 'template.json', 2_140),
    entry(`library/${TALKING.path}`, 'about.json', 1_380),
    entry(`library/${TALKING.path}`, 'guide.json', 3_020),
  ],
  [`library/${TALKING.path}/workflows`]: [
    entry(`library/${TALKING.path}/workflows`, 'person_still.api.json', 14_820),
    entry(`library/${TALKING.path}/workflows`, 'person_still.json', 38_410),
    entry(`library/${TALKING.path}/workflows`, 'install_person_still.py', 6_200),
    entry(`library/${TALKING.path}/workflows`, 'talking_clip.api.json', 22_960),
    entry(`library/${TALKING.path}/workflows`, 'talking_clip.json', 61_300),
    entry(`library/${TALKING.path}/workflows`, 'upscale.api.json', 7_400),
  ],
  [`library/${POSTER.path}`]: [entry(`library/${POSTER.path}`, 'template.json', 940)],
  [`library/${POSTER.path}/workflows`]: [
    entry(`library/${POSTER.path}/workflows`, 'poster.api.json', 11_250),
    entry(`library/${POSTER.path}/workflows`, 'poster.json', 29_870),
  ],
  // The broken one: its workflows survived, its template.json did not.
  [`library/${BROKEN.path}/workflows`]: [entry(`library/${BROKEN.path}/workflows`, 'product.api.json', 9_480)],
}

const client = {
  request: async (method: string, params?: { path?: string }) =>
    method === 'workspace.list' ? { entries: LISTINGS[params?.path || ''] || [] } : {},
  on: () => () => {},
  onStatus: () => () => {},
}
const noop = () => {}

export const SavedWithInputs = () => (
  <div style={{ maxWidth: 640 }}>
    <LibraryTemplateCard item={TALKING} client={client as any} busy={false} onUse={noop} onDelete={noop} />
  </div>
)

export const UploadedOneStep = () => (
  <div style={{ maxWidth: 640 }}>
    <LibraryTemplateCard item={POSTER} client={client as any} busy={false} onUse={noop} onDelete={noop} />
  </div>
)

export const MissingManifest = () => (
  <div style={{ maxWidth: 640 }}>
    <LibraryTemplateCard item={BROKEN} client={client as any} busy={false} onUse={noop} onDelete={noop} />
  </div>
)

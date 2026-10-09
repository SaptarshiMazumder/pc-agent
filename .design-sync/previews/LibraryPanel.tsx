/* LibraryPanel — the Library tab: the one folder every chat shares. Sections by who put things
 * there — Templates first, then Uploaded (from this computer), then Saved from chats (with a
 * chip per source chat once there are two). A workflow is drawn as the Workspace's own card
 * (files, installer, Use / Run again / View graph, a note); a reference or file is a row with
 * Use (into a slot of this chat) or open / delete.
 *
 * Fixture: the panel reads the catalogue through agentd/library.ts and its children list their
 * own folders — every `workspace.list` is answered by a per-cell client stub, and the daemon's
 * /file endpoint is stubbed on this page's fetch for exactly the index.json / template.json
 * paths below (anything else passes through). Reference thumbnails hit /thumbnail, which can't
 * answer offline: the rows fall back to their kind glyph, the panel's own offline face.
 * Cells sweep: a shelf of templates + references from two chats (chips), a kept workflow with its
 * installer and note (Uploaded empty), picking a reference for a waiting @face slot (plus a plain file row), and a brand-new empty Library.
 * Drag-over, notices and the delete prompt are interaction-only — not shown. */
import { LibraryPanel } from 'agent-app'

const WS = '/srv/agentd/workspace'

// The panel's first frame comes from a sessionStorage snapshot; cells must not seed each other.
try {
  sessionStorage.removeItem('comfy-library-v1')
} catch {
  /* no storage — nothing to clear */
}

type Origin = 'saved' | 'uploaded'
const TALK = 'Talking portrait for the launch'
const SNEAKER = 'Sneaker drop product shots'
const fromChat = (title: string) => ({ chat: `chat-17283${title.length}205-k3v9`, title })

const wf = (name: string, origin: Origin, opts: { slots?: string[]; from?: string; note?: string; v?: number } = {}) => ({
  id: `wf_${name.replace(/[^a-z0-9]/g, '').slice(0, 12)}`,
  kind: 'workflow',
  origin,
  name,
  note: opts.note || '',
  path: `${origin}/workflows/${name}`,
  versions: Array.from({ length: opts.v || 1 }, (_, i) => ({ v: i + 1, at: `2026-10-0${i + 3}T11:00:00.000Z`, slots: opts.slots })),
  ...(opts.from ? { from: fromChat(opts.from) } : {}),
  created: '2026-10-03T11:00:00.000Z',
})

const single = (
  kind: 'reference' | 'file',
  name: string,
  ext: string,
  origin: Origin,
  opts: { from?: string; note?: string } = {},
) => ({
  id: `${kind === 'reference' ? 'ref' : 'file'}_${name.replace(/[^a-z0-9]/g, '').slice(0, 12)}`,
  kind,
  origin,
  name,
  note: opts.note || '',
  path: `${origin}/${kind === 'reference' ? 'references' : 'files'}/${name}${ext}`,
  versions: [],
  ...(opts.from ? { from: fromChat(opts.from) } : {}),
  created: '2026-10-05T09:30:00.000Z',
})

const TEMPLATE = {
  id: 'tpl_talkingportr',
  kind: 'template',
  origin: 'saved',
  name: 'talking-portrait-launch',
  note: '',
  path: 'saved/templates/talking-portrait-launch',
  versions: [],
  from: fromChat(TALK),
  created: '2026-10-04T16:02:00.000Z',
}

const MANIFEST = {
  format: 'comfy-penguin-template',
  version: 2,
  name: 'talking-portrait-launch',
  description: 'Flux portrait of your face, animated into a lip-synced Wan 2.2 clip.',
  created: '2026-10-04T16:02:00.000Z',
  inputs: [
    { role: 'face', what: 'a front-facing photo' },
    { role: 'voice', what: 'a 5–10 s voice recording' },
  ],
  steps: [
    { role: 'person_still', api: 'workflows/person_still.api.json', ui: 'workflows/person_still.json', installer: [], slots: ['face'] },
    { role: 'talking_clip', api: 'workflows/talking_clip.api.json', installer: [], slots: ['voice'] },
  ],
}

const entry = (dir: string, name: string, size: number, kind = 'file') => ({ name, kind, size, path: `${WS}/${dir}/${name}` })

/** One cell's world: its catalogue and its folder listings (workspace-relative path → entries). */
type World = { items: unknown[]; listings: Record<string, unknown[]> }

const TPL_DIR = 'library/saved/templates/talking-portrait-launch'
const WAN_DIR = 'library/saved/workflows/wan22-talking-portrait/v3'

const WORLDS: Record<string, World> = {
  shelf: {
    items: [
      single('reference', 'studio-face-front', '.jpg', 'uploaded', { note: 'the one with even light' }),
      TEMPLATE,
      single('reference', 'person-still-00002', '.png', 'saved', { from: TALK, note: 'the keeper' }),
      single('reference', 'sneaker-hero-marble', '.png', 'saved', { from: SNEAKER }),
    ],
    listings: {
      'library/uploaded/references': [entry('library/uploaded/references', 'studio-face-front.jpg', 412_000, 'image')],
      'library/saved/references': [
        entry('library/saved/references', 'person-still-00002.png', 2_280_000, 'image'),
        entry('library/saved/references', 'sneaker-hero-marble.png', 1_940_000, 'image'),
      ],
      [TPL_DIR]: [entry(TPL_DIR, 'template.json', 1_540)],
      [`${TPL_DIR}/workflows`]: [
        entry(`${TPL_DIR}/workflows`, 'person_still.api.json', 14_820),
        entry(`${TPL_DIR}/workflows`, 'person_still.json', 38_410),
        entry(`${TPL_DIR}/workflows`, 'talking_clip.api.json', 22_960),
      ],
    },
  },
  workflows: {
    items: [
      wf('wan22-talking-portrait', 'saved', { slots: ['face', 'voice'], from: TALK, note: '5 s, 16 fps — good lip sync', v: 3 }),
    ],
    listings: {
      [WAN_DIR]: [
        entry(WAN_DIR, 'wan22-talking-portrait.api.json', 22_960),
        entry(WAN_DIR, 'wan22-talking-portrait.json', 61_300),
        entry(WAN_DIR, 'install_wan22-talking-portrait.py', 6_200),
        entry(WAN_DIR, 'install_wan22-talking-portrait.manifest.json', 1_100),
      ],
    },
  },
  pick: {
    items: [
      single('reference', 'studio-face-front', '.jpg', 'uploaded'),
      single('reference', 'studio-face-three-quarter', '.jpg', 'uploaded'),
      single('file', 'brand-palette', '.txt', 'uploaded', { note: 'hex codes for the poster text' }),
      single('reference', 'person-still-00002', '.png', 'saved', { from: TALK }),
    ],
    listings: {
      'library/uploaded/references': [
        entry('library/uploaded/references', 'studio-face-front.jpg', 412_000, 'image'),
        entry('library/uploaded/references', 'studio-face-three-quarter.jpg', 398_000, 'image'),
      ],
      'library/saved/references': [entry('library/saved/references', 'person-still-00002.png', 2_280_000, 'image')],
    },
  },
  empty: { items: [], listings: {} },
}

const indexPath = (cell: string) => `${WS}/library/.${cell}/index.json`

// The daemon's /file endpoint, for the catalogue and template manifest paths only.
const JSON_FILES: Record<string, unknown> = { [`${WS}/${TPL_DIR}/template.json`]: MANIFEST }
for (const [cell, world] of Object.entries(WORLDS)) JSON_FILES[indexPath(cell)] = { version: 1, items: world.items }

const realFetch = window.fetch.bind(window)
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const m = /[?&]path=([^&]+)/.exec(url)
  const path = m ? decodeURIComponent(m[1]) : ''
  if (path in JSON_FILES) {
    return new Response(JSON.stringify(JSON_FILES[path]), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  return realFetch(input, init)
}) as typeof fetch

const clientFor = (cell: string) => ({
  request: async (method: string, params?: { path?: string }) => {
    if (method !== 'workspace.list') return {}
    const path = params?.path || ''
    if (path === 'library') {
      return cell === 'empty'
        ? { entries: [] }
        : { entries: [{ name: 'index.json', kind: 'file', size: 3_800, path: indexPath(cell) }] }
    }
    return { entries: WORLDS[cell].listings[path] || [] }
  },
  on: () => () => {},
  onStatus: () => () => {},
})

const SESSION = 'chat-1728391205-k3v9'
const SLOTS = [
  { role: 'face', what: 'a front-facing photo, even light', workflows: ['person_still'], file: null, fedBy: null },
  { role: 'voice', what: 'a 5–10 s voice recording', workflows: ['talking_clip'], file: null, fedBy: null },
]
const noop = () => {}

const panel = (cell: string, extra: { slots?: unknown[]; targetRole?: string } = {}) => (
  <div style={{ maxWidth: 640 }}>
    <LibraryPanel
      client={clientFor(cell) as any}
      sessionKey={SESSION}
      slots={(extra.slots || []) as any}
      running={false}
      workspaceVersion={1}
      targetRole={extra.targetRole || ''}
      onClearTarget={noop}
      onUseWorkflow={noop}
      onRunAgain={noop}
      onUseTemplate={noop}
    />
  </div>
)

export const Shelf = () => panel('shelf')

export const KeptWorkflows = () => panel('workflows')

export const PickingForFace = () => panel('pick', { slots: SLOTS, targetRole: 'face' })

export const EmptyLibrary = () => panel('empty')

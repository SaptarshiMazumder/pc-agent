/* StarterWorkflows — "Start from your Library": under the composer on an empty chat, the newest
 * four kept workflows, each with the Library's own Run again. The caption is the item's note,
 * else the slots its latest version needs, else the chat it was saved from.
 *
 * Fixture: the component reads the catalogue through agentd/library.ts — `workspace.list` on
 * `library/` finds index.json, then the window fetches it from the daemon's /file endpoint. The
 * client stub answers the listing, and this page's fetch is stubbed for exactly that index path
 * (anything else passes through untouched), with a realistic LibraryIndex. Each cell's client
 * lists its own index path, so the cells hold different catalogues.
 * Cells sweep: a full shelf (6 workflows + references → newest 4 shown, all three caption kinds)
 * and a Library with a single kept workflow. An empty Library renders nothing (by design) and
 * has no cell. */
import { StarterWorkflows } from 'agent-app'

const WS = '/srv/agentd/workspace'

const wf = (
  name: string,
  opts: { note?: string; slots?: string[]; from?: string; origin?: 'saved' | 'uploaded'; v?: number },
) => ({
  id: `wf_${name.replace(/[^a-z0-9]/g, '').slice(0, 12)}`,
  kind: 'workflow',
  origin: opts.origin || 'saved',
  name,
  note: opts.note || '',
  path: `${opts.origin || 'saved'}/workflows/${name}`,
  versions: Array.from({ length: opts.v || 1 }, (_, i) => ({
    v: i + 1,
    at: '2026-10-0' + (i + 2) + 'T14:20:00.000Z',
    slots: opts.slots,
  })),
  ...(opts.from ? { from: { chat: 'chat-1728391205-k3v9', title: opts.from } } : {}),
  created: '2026-10-02T14:20:00.000Z',
})

const ref = (name: string) => ({
  id: `ref_${name.replace(/[^a-z0-9]/g, '')}`,
  kind: 'reference',
  origin: 'uploaded',
  name,
  note: '',
  path: `uploaded/references/${name}.jpg`,
  versions: [],
  created: '2026-10-01T09:00:00.000Z',
})

/** Oldest first — the catalogue is in the order things were kept. */
const INDEXES: Record<string, unknown> = {
  [`${WS}/library/full/index.json`]: {
    version: 1,
    items: [
      wf('sdxl-product-on-marble', { origin: 'uploaded' }),
      ref('studio-face-front'),
      wf('flux-portrait', { note: 'the 1024×1536 headshot look — soft key light', v: 2 }),
      wf('wan22-talking-portrait', { slots: ['face', 'voice'], from: 'Talking portrait for the launch', v: 3 }),
      ref('sneaker-hero-shot'),
      wf('ultimate-sd-upscale-4x', { from: 'Upscale the poster renders' }),
      wf('qwen-image-poster-text', { slots: ['product'], from: 'Autumn sale poster' }),
    ],
  },
  [`${WS}/library/one/index.json`]: {
    version: 1,
    items: [wf('flux-portrait', { slots: ['face'], from: 'Headshots for the team page' })],
  },
}

// The daemon's /file endpoint, for these index paths only.
const realFetch = window.fetch.bind(window)
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const m = /[?&]path=([^&]+)/.exec(url)
  const path = m ? decodeURIComponent(m[1]) : ''
  if (path in INDEXES) {
    return new Response(JSON.stringify(INDEXES[path]), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  return realFetch(input, init)
}) as typeof fetch

const clientFor = (which: string) => ({
  request: async (method: string, params?: { path?: string }) =>
    method === 'workspace.list' && params?.path === 'library'
      ? {
          entries: [
            { name: 'index.json', kind: 'file', size: 4_210, path: `${WS}/library/${which}/index.json` },
            { name: 'saved', kind: 'folder', path: `${WS}/library/${which}/saved` },
            { name: 'uploaded', kind: 'folder', path: `${WS}/library/${which}/uploaded` },
          ],
        }
      : { entries: [] },
  on: () => () => {},
  onStatus: () => () => {},
})

const full = clientFor('full')
const one = clientFor('one')
const noop = () => {}

export const FullShelf = () => <StarterWorkflows client={full as any} workspaceVersion={1} onRunAgain={noop} />

export const OneKept = () => <StarterWorkflows client={one as any} workspaceVersion={1} onRunAgain={noop} />

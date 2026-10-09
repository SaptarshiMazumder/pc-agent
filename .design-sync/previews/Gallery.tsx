/* Gallery — every render this account has made, filed by the chat that made it: a page head
 * (title, "N conversations · newest first", the Images & videos | Workflows segment), then one
 * section per chat, newest first — the header opens that chat — with a wall of 4:5 tiles
 * (kind tag, name, size, Save to Library, Delete). Files whose chat was deleted are kept, in one
 * "Deleted conversations" section at the end (no link: there is no chat to open).
 *
 * Sections come from the daemon's folders, not the transcript: the client is stubbed to answer
 * `workspace.list` for `outputs/` (one folder per chat) and each chat folder, exactly as
 * agentd/chat-library.ts reads them. The `chats` prop (the store's ChatRow list) titles them.
 *
 * Thumbnails: tiles draw the daemon's `/thumbnail` (falling back to `/file`), which do not
 * exist offline, and this component has no placeholder glyph — a broken <img> would be the
 * only offline face. So this preview answers those two URLs (and only on its own card page)
 * with flat painted stand-ins, the image half of the daemon stub. Video tiles are avoided
 * (an empty <video>). The Workflows shelf is a click on the segment (internal state); the
 * WorkflowCard it draws has its own card. The lightbox and delete prompt follow clicks.
 *
 * Cells: populated (two chats, page scrolls past the first rows — the page is a fixed-height
 * scroller as in the app); a chat plus the Deleted-conversations section; nothing made yet;
 * and the first read still in flight. */
import { Gallery } from 'agent-app'

/* ── the daemon's /thumbnail + /file, stubbed for this card's <img>s ─────────────────────── */
const PALETTES = [
  ['#3b2a22', '#c98a5a', '#f2c79a'],
  ['#1d2433', '#4d6a9a', '#c9d6ef'],
  ['#20302a', '#5f8f6e', '#d8e8c8'],
  ['#2e1f33', '#8c5aa8', '#f0c6e4'],
  ['#332a1a', '#b08a3e', '#f4e2b0'],
  ['#1a2a30', '#3f8a9a', '#bfe6ee'],
]
function standIn(url: string): string {
  const name = new URL(url).searchParams.get('path') || url
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  const [a, b, c] = PALETTES[h % PALETTES.length]
  const cx = 30 + (h % 40)
  // A subject that matches the file: a bust for portraits, a mug for the product shots, a
  // skyline for the street scene.
  const subject = /mug/.test(name)
    ? `<rect x="130" y="190" width="140" height="170" rx="18" fill="${c}" opacity=".85"/><ellipse cx="200" cy="190" rx="70" ry="16" fill="${a}" opacity=".5"/><path d="M270 225 C330 225 330 320 270 320" stroke="${c}" stroke-width="18" fill="none" opacity=".85"/><rect x="0" y="360" width="400" height="140" fill="${a}" opacity=".45"/>`
    : /street|neon/.test(name)
      ? `<rect x="0" y="120" width="90" height="380" fill="${a}" opacity=".8"/><rect x="300" y="80" width="100" height="420" fill="${a}" opacity=".8"/><rect x="110" y="200" width="70" height="300" fill="${a}" opacity=".6"/><rect x="215" y="170" width="70" height="330" fill="${a}" opacity=".6"/><rect x="20" y="160" width="40" height="10" fill="${c}"/><rect x="320" y="130" width="50" height="10" fill="${c}"/><rect x="0" y="440" width="400" height="60" fill="${c}" opacity=".25"/>`
      : `<ellipse cx="200" cy="230" rx="72" ry="92" fill="${a}" opacity=".55"/><path d="M70 500 C90 360 310 360 330 500Z" fill="${a}" opacity=".6"/>`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${b}"/><stop offset="1" stop-color="${a}"/></linearGradient><radialGradient id="l" cx="${cx}%" cy="30%" r="60%"><stop offset="0" stop-color="${c}" stop-opacity=".75"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient></defs><rect width="400" height="500" fill="url(#g)"/><rect width="400" height="500" fill="url(#l)"/>${subject}</svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}
const realSetAttribute = Element.prototype.setAttribute
Element.prototype.setAttribute = function (this: Element, name: string, value: string) {
  if (this.tagName === 'IMG' && name === 'src' && /\/(thumbnail|file)\?/.test(String(value))) {
    return realSetAttribute.call(this, name, standIn(String(value)))
  }
  return realSetAttribute.call(this, name, value)
}

/* ── the workspace, as workspace.list answers it ─────────────────────────────────────────── */
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

type Entry = { name: string; kind: string; size?: number; modified?: number; rel?: string; path?: string }
const file = (folder: string, name: string, kind: 'image' | 'audio' | 'file', size: number, age: number): Entry => ({
  name,
  kind,
  size,
  modified: now - age,
  rel: `outputs/${folder}/${name}`,
  path: `${WS}/outputs/${folder}/${name}`,
})

const PORTRAIT = 'chat-1728391205-k3v9'
const MUG = 'chat-1728300112-a8d2'
const GONE = 'chat-1727001234-zz01'

const FOLDERS: Record<string, Entry[]> = {
  [PORTRAIT]: [
    file(PORTRAIT, 'person_still_00003.png', 'image', 2_340_000, 900),
    file(PORTRAIT, 'person_still_00002.png', 'image', 2_280_000, 960),
    file(PORTRAIT, 'person_still_00001.png', 'image', 2_310_000, 1800),
    file(PORTRAIT, 'voice_line_trimmed.wav', 'audio', 880_000, 2000),
  ],
  [MUG]: [
    file(MUG, 'mug_linen_hero_00001.png', 'image', 1_920_000, 7300),
    file(MUG, 'mug_linen_hero_00002.png', 'image', 1_870_000, 7400),
    file(MUG, 'mug_steam_closeup_00001.png', 'image', 1_640_000, 7600),
  ],
  [GONE]: [
    file(GONE, 'anime_neon_street_00001.png', 'image', 2_050_000, 86400 * 12),
    file(GONE, 'anime_neon_street_00002.png', 'image', 2_010_000, 86400 * 12),
  ],
}

const daemon = (folders: string[], pending = false) =>
  ({
    request: (method: string, params?: { path?: string }) => {
      if (pending) return new Promise(() => {})
      if (method !== 'workspace.list') return Promise.resolve({})
      const p = params?.path || ''
      if (p === 'outputs') return Promise.resolve({ entries: folders.map((f) => ({ name: f, kind: 'folder' })) })
      const folder = p.split('/')[1]
      return Promise.resolve({ entries: folders.includes(folder) ? FOLDERS[folder] : [] })
    },
    on: () => () => {},
    onStatus: () => () => {},
  }) as any

const CHATS = [
  { sessionId: PORTRAIT, title: 'Rooftop portrait at golden hour, 1024×1536', messages: 14, modified: now - 300 },
  { sessionId: MUG, title: 'Product ad — ceramic mug on linen', messages: 22, modified: now - 7200 },
]

const noop = () => {}
/* A fixed-height page, as the app's main column is: the section list scrolls inside it. The
   empty and loading cells are shorter — the same page with nothing below the head. */
const Page = ({ children, height = 640 }: { children: React.ReactNode; height?: number }) => (
  <div style={{ height, display: 'flex', flexDirection: 'column' }}>{children}</div>
)

const twoChats = daemon([PORTRAIT, MUG])
export const Renders = () => (
  <Page>
    <Gallery client={twoChats} chats={CHATS} workspaceVersion={1} onOpenChat={noop} />
  </Page>
)

const withOrphans = daemon([MUG, GONE])
export const WithDeletedConversations = () => (
  <Page>
    <Gallery client={withOrphans} chats={CHATS} workspaceVersion={1} onOpenChat={noop} />
  </Page>
)

const nothing = daemon([])
export const NothingMadeYet = () => (
  <Page height={260}>
    <Gallery client={nothing} chats={CHATS} workspaceVersion={1} onOpenChat={noop} />
  </Page>
)

const reading = daemon([], true)
export const ReadingFolders = () => (
  <Page height={220}>
    <Gallery client={reading} chats={CHATS} workspaceVersion={1} onOpenChat={noop} />
  </Page>
)

/* The Library — the one folder every chat shares, and the window is its only writer.
 *
 * WHAT IT IS. `library/` inside the agent's workspace: `uploaded/` for what the person brought
 * from their own machine, `saved/` for what came out of a chat, and `index.json` — one record
 * per item — as the catalogue. Three kinds: a WORKFLOW (a folder of versions, each holding the
 * api.json, the editor json and the installer files that existed when it was saved), a REFERENCE
 * (an image or video reused as workflow input) and a FILE (anything else the agent may read).
 *
 * WHY THE WINDOW WRITES AND THE AGENT ONLY READS. Every way into the Library is a person's act —
 * a drop on the tab, a Save button, a paste that turned out not to be an image — and the agent's
 * three tools (library_find / read / use, plugins/comfy-bridge) only ever read the catalogue and
 * copy OUT of it into the chat. One writer means no merge to get wrong, and "what is in my
 * Library" is always exactly what the person put there.
 *
 * EVERYTHING GOES THROUGH THE DAEMON'S WORKSPACE RPCS — list, upload, copy, delete — the same
 * doors the References panel and the file rail already use. A render going into the Library is
 * a `workspace.copy` on the daemon's own disk, not a download-and-upload through the browser;
 * the index is rewritten in place with `overwrite`. Nothing here needs a new server.
 *
 * ORIGIN IS A FOLDER, NOT A FLAG: a glance at the disk says the same thing the tab does. The
 * chat a saved item came from is a caption on the record (title captured at save time, so a
 * deleted conversation keeps its name), never a folder to dig through.
 */

import type { AgentdClient } from '@agentd/client'

import { fileUrl, type Artifact, type ArtifactKind } from './artifacts'
import { AGENT_ID } from './client'
import { chatDirFor } from './workspace-files'

export type LibraryKind = 'workflow' | 'reference' | 'file' | 'template'
export type LibraryOrigin = 'uploaded' | 'saved'

export interface LibraryVersion {
  v: number
  at: string
  /** The slot roles the graph declares (`@model` …), read at save time so "Run again" can ask
   *  for them before any agent turn. */
  slots?: string[]
}

export interface LibraryItem {
  id: string
  kind: LibraryKind
  origin: LibraryOrigin
  name: string
  note: string
  /** Library-relative posix path: a folder for a workflow, a file for the other kinds. */
  path: string
  versions: LibraryVersion[]
  /** Where a saved item came from. Absent on an upload. */
  from?: { chat: string; title: string }
  /** A saved single file: its workspace-relative path in that chat, and its size then — what
   *  "is this already in the Library" is answered by. Absent on older items and uploads. */
  source?: string
  size?: number
  created: string
}

/** What one Save/Add to Library press did: every button says one of these. */
export interface LibrarySaveOutcome {
  /** One sentence for the note under the button. */
  message: string
  /** 'already' only when NOTHING new went in — the button then reads "Already in Library". */
  state: 'saved' | 'already'
}

/** What `saveFromChat` did: what went in, and what was already there unchanged. */
export interface LibrarySaveResult {
  added: LibraryItem[]
  unchanged: LibraryItem[]
}

/** The result as the sentence and state every save button shows. */
export function saveOutcome(res: LibrarySaveResult): LibrarySaveOutcome {
  const { added, unchanged } = res
  if (!added.length && unchanged.length) {
    return {
      state: 'already',
      message:
        unchanged.length === 1
          ? `${unchanged[0].name} is already in your Library`
          : `All ${unchanged.length} are already in your Library`,
    }
  }
  const first = added.length === 1 ? `Added ${added[0].name} to the Library` : `Added ${added.length} items to the Library`
  return { state: 'saved', message: unchanged.length ? `${first} · ${unchanged.length} already there` : first }
}

export interface LibraryIndex {
  version: 1
  items: LibraryItem[]
}

export const LIBRARY_DIR = 'library'
const INDEX_NAME = 'index.json'
export const KIND_DIR: Record<LibraryKind, string> = {
  workflow: 'workflows',
  reference: 'references',
  file: 'files',
  // A folder per template: template.json, its workflows/, a thumbnail (library-template.ts).
  template: 'templates',
}
const MEDIA: ArtifactKind[] = ['image', 'video', 'audio']

/** What a saved or uploaded file is for the Library: a workflow, an input, or just a file. */
export function kindForName(name: string, artifactKind?: ArtifactKind): LibraryKind {
  if (artifactKind && MEDIA.includes(artifactKind)) return 'reference'
  if (/\.(png|jpe?g|webp|gif|bmp|tiff?|mp4|mov|webm|mkv|avi|mp3|wav|flac|ogg)$/i.test(name)) {
    return 'reference'
  }
  if (name.endsWith('.api.json')) return 'workflow'
  return 'file'
}

/** A `.json` is a workflow only when its contents are a ComfyUI graph — API format (id →
 *  class_type) or the editor's own save (`nodes[]`). Anything else is a file. */
export function looksLikeGraph(text: string): boolean {
  try {
    const g = JSON.parse(text)
    if (!g || typeof g !== 'object') return false
    if (Array.isArray((g as { nodes?: unknown }).nodes)) return true
    const values = Object.values(g as Record<string, unknown>)
    return (
      values.length > 0 &&
      values.every((v) => !!v && typeof v === 'object' && 'class_type' in (v as object))
    )
  } catch {
    return false
  }
}

/** The slot roles an API-format graph declares — `LoadImage.image = "@model"` and the like. */
export function slotsInGraph(text: string): string[] {
  const out = new Set<string>()
  try {
    const g = JSON.parse(text) as Record<string, { inputs?: Record<string, unknown> }>
    for (const node of Object.values(g || {})) {
      for (const v of Object.values(node?.inputs || {})) {
        if (typeof v === 'string' && v.startsWith('@') && v.length > 1) out.add(v.slice(1).trim())
      }
    }
  } catch {
    /* not a graph — no slots */
  }
  return [...out].sort()
}

/** `flux-portrait.api.json`, `flux-portrait.json`, `install_flux-portrait.py` and its manifest
 *  are one workflow named `flux-portrait`. Anything else answers null. */
export function workflowBase(name: string): string | null {
  let m = name.match(/^install_(.+)\.manifest\.json$/)
  if (m) return m[1]
  m = name.match(/^install_(.+)\.py$/)
  if (m) return m[1]
  if (name.endsWith('.api.json')) return name.slice(0, -'.api.json'.length)
  if (name.endsWith('.json')) return name.slice(0, -'.json'.length)
  return null
}

export function slug(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'item'
  )
}

export function newId(kind: LibraryKind): string {
  const prefix = kind === 'workflow' ? 'wf' : kind === 'reference' ? 'ref' : kind === 'template' ? 'tpl' : 'file'
  const bytes = new Uint8Array(6)
  crypto.getRandomValues(bytes)
  return `${prefix}_${[...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')}`
}

export function utf8Base64(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}

// ---------------------------------------------------------------------------- the daemon

type Entry = { name?: unknown; kind?: unknown; size?: unknown; rel?: unknown; path?: unknown }

async function list(client: AgentdClient, path: string): Promise<Entry[]> {
  const res = (await client.request('workspace.list', { agentId: AGENT_ID, path })) as {
    entries?: Entry[]
  }
  return Array.isArray(res?.entries) ? res.entries : []
}

export async function copy(client: AgentdClient, from: string, to: string, overwrite = false): Promise<void> {
  const res = (await client.request('workspace.copy', {
    agentId: AGENT_ID,
    from,
    to,
    overwrite,
  })) as { ok?: boolean; error?: string }
  if (!res?.ok) throw new Error(res?.error || `could not copy ${from}`)
}

export async function upload(
  client: AgentdClient,
  dir: string,
  name: string,
  dataBase64: string,
  overwrite = false,
): Promise<string> {
  const res = (await client.request('workspace.upload', {
    agentId: AGENT_ID,
    path: dir,
    name,
    dataBase64,
    overwrite,
  })) as { ok?: boolean; name?: string; error?: string }
  if (!res?.ok) throw new Error(res?.error || `could not upload ${name}`)
  return String(res.name || name)
}

async function remove(client: AgentdClient, rel: string): Promise<void> {
  const res = (await client.request('workspace.delete', { agentId: AGENT_ID, path: rel })) as {
    ok?: boolean
    error?: string
  }
  if (!res?.ok && res?.error !== 'not found') throw new Error(res?.error || `could not delete ${rel}`)
}

/** The Library's files as artifacts — the entries of one folder, absolute paths included, so
 *  `fileUrl` can open them. */
export async function listArtifacts(client: AgentdClient, dir: string): Promise<Artifact[]> {
  const entries = await list(client, dir)
  return entries
    .filter((e) => e && e.kind !== 'folder' && e.path)
    .map((e) => ({
      path: String(e.path),
      name: String(e.name || ''),
      mime: '',
      kind: (MEDIA.includes(e.kind as ArtifactKind) ? e.kind : 'file') as ArtifactKind,
      size: Number(e.size || 0),
    }))
}

// ---------------------------------------------------------------------------- the catalogue

function normalise(raw: unknown): LibraryIndex {
  const items: LibraryItem[] = []
  const list = (raw as { items?: unknown })?.items
  for (const it of Array.isArray(list) ? list : []) {
    const r = it as Partial<LibraryItem> & { from?: unknown }
    if (!r || !r.id || !r.name || !r.path) continue
    if (!['workflow', 'reference', 'file', 'template'].includes(String(r.kind))) continue
    if (!['uploaded', 'saved'].includes(String(r.origin))) continue
    const from = r.from as { chat?: unknown; title?: unknown } | undefined
    items.push({
      id: String(r.id),
      kind: r.kind as LibraryKind,
      origin: r.origin as LibraryOrigin,
      name: String(r.name),
      note: String(r.note || ''),
      path: String(r.path).replace(/^\/+|\/+$/g, ''),
      versions: Array.isArray(r.versions)
        ? r.versions.map((v) => ({
            v: Number((v as LibraryVersion).v || 0),
            at: String((v as LibraryVersion).at || ''),
            slots: Array.isArray((v as LibraryVersion).slots)
              ? (v as LibraryVersion).slots!.map(String)
              : undefined,
          }))
        : [],
      ...(from && from.chat ? { from: { chat: String(from.chat), title: String(from.title || '') } } : {}),
      ...(r.source ? { source: String(r.source) } : {}),
      ...(typeof r.size === 'number' ? { size: r.size } : {}),
      created: String(r.created || ''),
    })
  }
  return { version: 1, items }
}

export function latestVersion(item: LibraryItem): number {
  return item.versions.reduce((m, v) => Math.max(m, v.v), 0)
}

/** Read the catalogue. No Library, or no index yet, is an empty one. */
export async function readIndex(client: AgentdClient): Promise<LibraryIndex> {
  const entries = await list(client, LIBRARY_DIR)
  const idx = entries.find((e) => String(e.name) === INDEX_NAME && e.path)
  if (!idx) return { version: 1, items: [] }
  const res = await fetch(fileUrl(String(idx.path)), { cache: 'no-store' })
  if (!res.ok) throw new Error(`could not read the Library index (HTTP ${res.status})`)
  return normalise(await res.json().catch(() => ({})))
}

export async function writeIndex(client: AgentdClient, index: LibraryIndex): Promise<void> {
  await upload(client, LIBRARY_DIR, INDEX_NAME, utf8Base64(JSON.stringify(index, null, 2) + '\n'), true)
}

export function nowIso(): string {
  return new Date().toISOString()
}

/** A name nobody else in this kind+origin holds: `face`, then `face-2`, `face-3`. */
function freeName(index: LibraryIndex, kind: LibraryKind, origin: LibraryOrigin, wanted: string): string {
  const taken = new Set(
    index.items.filter((i) => i.kind === kind && i.origin === origin).map((i) => i.name.toLowerCase()),
  )
  if (!taken.has(wanted.toLowerCase())) return wanted
  for (let n = 2; ; n++) {
    const candidate = `${wanted}-${n}`
    if (!taken.has(candidate.toLowerCase())) return candidate
  }
}

/** Split `name.ext` keeping the extension for a single-file item. */
export function splitExt(name: string): { stem: string; ext: string } {
  const m = name.match(/^(.*?)(\.[A-Za-z0-9]+)?$/)
  return { stem: m?.[1] || name, ext: (m?.[2] || '').toLowerCase() }
}

// ---------------------------------------------------------------------------- saving from a chat

/** One file of this chat, by its workspace-relative path — what the rail's rows and the
 *  creations screen both know. */
export interface ChatFile {
  rel: string
  name: string
  kind: ArtifactKind
  /** Absolute path, when known — lets a workflow's slots be read before it is saved. */
  path?: string
  /** Bytes, when known — half of the "already saved" check for a single file. */
  size?: number
}

async function textOf(path: string): Promise<string> {
  const res = await fetch(fileUrl(path), { cache: 'no-store' })
  if (!res.ok) throw new Error(`could not read ${path} (HTTP ${res.status})`)
  return res.text()
}

/**
 * Put files that came out of a chat into `library/saved/`. Workflows are gathered by name — the
 * api.json, the editor json and the installer files travel as one versioned item; a name that is
 * already in the Library gets a new version rather than a copy. References and files are single
 * items, deduped by name.
 *
 * NOTHING IS SAVED TWICE. A workflow whose run file is byte-identical to its newest Library
 * version is left alone (a press used to add an empty v2, v3 … v10 each time); an installer it
 * gained since is added to that same version. A single file already saved from this chat, same
 * path and size, is left alone too. Both come back in `unchanged`, so the button can say so.
 */
export async function saveFromChat(
  client: AgentdClient,
  files: ChatFile[],
  from: { chat: string; title: string },
): Promise<LibrarySaveResult> {
  const index = await readIndex(client)
  const at = nowIso()
  const added: LibraryItem[] = []
  const unchanged: LibraryItem[] = []

  // Workflows first, grouped by base name.
  const byBase = new Map<string, ChatFile[]>()
  const singles: ChatFile[] = []
  for (const f of files) {
    const base = MEDIA.includes(f.kind) ? null : workflowBase(f.name)
    if (base) byBase.set(base, [...(byBase.get(base) || []), f])
    else singles.push(f)
  }
  for (const [base, parts] of byBase) {
    const api = parts.find((p) => p.name.endsWith('.api.json'))
    // A lone editor json with no run file is still a workflow the person may want to keep; a
    // lone installer is not.
    if (!api && !parts.some((p) => p.name.endsWith('.json') && !p.name.endsWith('.manifest.json'))) {
      singles.push(...parts)
      continue
    }
    const name = slug(base)
    let item = index.items.find((i) => i.kind === 'workflow' && i.name === name)
    const apiText = api?.path ? await textOf(api.path) : ''
    if (item && api && apiText) {
      const kept = await libraryFiles(client, item)
      const keptApi = kept.find((k) => k.name.endsWith('.api.json'))
      if (keptApi && (await textOf(keptApi.path)) === apiText) {
        // SAME GRAPH: no new version. Only files the kept version lacks (an installer exported
        // after the first save) are added to it.
        const dir = `${LIBRARY_DIR}/${item.path}/v${latestVersion(item)}`
        const have = new Set(kept.map((k) => k.name))
        for (const p of parts) if (!have.has(p.name)) await copy(client, p.rel, `${dir}/${p.name}`, true)
        unchanged.push(item)
        continue
      }
    }
    const v = item ? latestVersion(item) + 1 : 1
    if (!item) {
      item = {
        id: newId('workflow'),
        kind: 'workflow',
        origin: 'saved',
        name,
        note: '',
        path: `saved/${KIND_DIR.workflow}/${name}`,
        versions: [],
        from,
        created: at,
      }
      index.items.push(item)
    }
    const dir = `${LIBRARY_DIR}/${item.path}/v${v}`
    for (const p of parts) await copy(client, p.rel, `${dir}/${p.name}`, true)
    const slots = apiText ? slotsInGraph(apiText) : undefined
    item.versions.push({ v, at, ...(slots ? { slots } : {}) })
    // A later save from another chat: the caption follows the newest version.
    item.from = from
    added.push(item)
  }

  for (const f of singles) {
    const kind = kindForName(f.name, f.kind)
    const { stem, ext } = splitExt(f.name)
    // Already saved from this chat? By its path there (older items: by name), and by size.
    const kept = index.items.find(
      (i) =>
        i.kind === kind &&
        i.origin === 'saved' &&
        i.from?.chat === from.chat &&
        (i.source ? i.source === f.rel : i.name === slug(stem)) &&
        (i.size === undefined || f.size === undefined || i.size === f.size),
    )
    if (kept) {
      unchanged.push(kept)
      continue
    }
    const name = freeName(index, kind, 'saved', slug(stem))
    const fileName = `${name}${ext}`
    const rel = `saved/${KIND_DIR[kind]}/${fileName}`
    await copy(client, f.rel, `${LIBRARY_DIR}/${rel}`, false)
    const item: LibraryItem = {
      id: newId(kind),
      kind,
      origin: 'saved',
      name,
      note: '',
      path: rel,
      versions: [],
      from,
      source: f.rel,
      ...(f.size !== undefined ? { size: f.size } : {}),
      created: at,
    }
    index.items.push(item)
    added.push(item)
  }

  // Written for an installer added to a kept version too: the catalogue itself did not change
  // then, but writing it is harmless, and one rule is simpler than two.
  if (added.length || unchanged.length) await writeIndex(client, index)
  return { added, unchanged }
}

// ---------------------------------------------------------------------------- uploading

async function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onerror = () => reject(r.error || new Error('read failed'))
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.readAsDataURL(file)
  })
}

/** What an uploaded file is: media is a reference; a `.json` that parses as a graph is a
 *  workflow; the rest are files. */
export async function kindOfUpload(file: File): Promise<LibraryKind> {
  if (file.type.startsWith('image/') || file.type.startsWith('video/') || file.type.startsWith('audio/')) {
    return 'reference'
  }
  const byName = kindForName(file.name)
  if (byName === 'reference' || byName === 'workflow') return byName
  if (file.name.endsWith('.json') && file.size < 8 * 1024 * 1024) {
    return looksLikeGraph(await file.text()) ? 'workflow' : 'file'
  }
  return 'file'
}

/** Put files from the person's machine into `library/uploaded/`. */
export async function uploadToLibrary(client: AgentdClient, files: File[]): Promise<LibraryItem[]> {
  const index = await readIndex(client)
  const at = nowIso()
  const added: LibraryItem[] = []
  for (const file of files) {
    const kind = await kindOfUpload(file)
    const data = await readBase64(file)
    if (kind === 'workflow') {
      const base = workflowBase(file.name) || splitExt(file.name).stem
      const name = slug(base)
      let item = index.items.find((i) => i.kind === 'workflow' && i.origin === 'uploaded' && i.name === name)
      const v = item ? latestVersion(item) + 1 : 1
      if (!item) {
        item = {
          id: newId('workflow'),
          kind: 'workflow',
          origin: 'uploaded',
          name,
          note: '',
          path: `uploaded/${KIND_DIR.workflow}/${name}`,
          versions: [],
          created: at,
        }
        index.items.push(item)
      }
      const text = await file.text()
      // The saved file keeps the workflow's own name so the agent's tools find `.api.json`.
      const isApi = file.name.endsWith('.api.json') || !Array.isArray((JSON.parse(text) as { nodes?: unknown }).nodes)
      const saved = await upload(client, `${LIBRARY_DIR}/${item.path}/v${v}`, isApi ? `${name}.api.json` : `${name}.json`, data, true)
      void saved
      item.versions.push({ v, at, ...(isApi ? { slots: slotsInGraph(text) } : {}) })
      added.push(item)
      continue
    }
    const { stem, ext } = splitExt(file.name)
    const name = freeName(index, kind, 'uploaded', slug(stem))
    const dir = `${LIBRARY_DIR}/uploaded/${KIND_DIR[kind]}`
    const saved = await upload(client, dir, `${name}${ext}`, data, false)
    const item: LibraryItem = {
      id: newId(kind),
      kind,
      origin: 'uploaded',
      name,
      note: '',
      path: `uploaded/${KIND_DIR[kind]}/${saved}`,
      versions: [],
      created: at,
    }
    index.items.push(item)
    added.push(item)
  }
  if (added.length) await writeIndex(client, index)
  return added
}

// ---------------------------------------------------------------------------- using and deleting

/** The files behind an item: a workflow version's folder, or the one file. */
export async function libraryFiles(
  client: AgentdClient,
  item: LibraryItem,
  version?: number,
): Promise<Artifact[]> {
  if (item.kind === 'workflow') {
    const v = version || latestVersion(item)
    return listArtifacts(client, `${LIBRARY_DIR}/${item.path}/v${v}`)
  }
  const parent = `${LIBRARY_DIR}/${item.path}`.split('/').slice(0, -1).join('/')
  const name = item.path.split('/').pop() || ''
  return (await listArtifacts(client, parent)).filter((a) => a.name === name)
}

/** Every Library reference's file, keyed by the catalogue's `path` (`saved/references/x.png`).
 *  TWO LISTINGS FOR THE WHOLE SHELF — uploaded and saved — rather than one per card, so the
 *  panel can draw thumbnails without a request per row. */
export async function referenceFiles(client: AgentdClient): Promise<Map<string, Artifact>> {
  const out = new Map<string, Artifact>()
  for (const origin of ['uploaded', 'saved'] as const) {
    const dir = `${origin}/${KIND_DIR.reference}`
    for (const a of await listArtifacts(client, `${LIBRARY_DIR}/${dir}`)) out.set(`${dir}/${a.name}`, a)
  }
  return out
}

/** Fill a slot of THIS chat with a Library reference: a copy on the daemon's disk into
 *  `references/<chat>/<role>.<ext>`, the same file the References panel would have uploaded. */
export async function useReferenceInChat(
  client: AgentdClient,
  item: LibraryItem,
  sessionKey: string,
  role: string,
): Promise<string> {
  const ext = splitExt(item.path.split('/').pop() || '').ext
  const clean = role.replace(/^@/, '').trim()
  const target = clean ? `${clean}${ext}` : item.path.split('/').pop() || item.name
  const to = `${chatDirFor('references', sessionKey)}/${target}`
  await copy(client, `${LIBRARY_DIR}/${item.path}`, to, true)
  return to
}

/** What the chat is told when the person presses "Use in this chat" on a workflow. The agent
 *  brings it in with `library_use`, which records its slots and marks the design as existing —
 *  the window does not copy workflow files itself, or the agent would never know they came. */
export function useWorkflowMessage(item: LibraryItem): string {
  return (
    `Use my Library workflow "${item.name}" (id ${item.id}) in this chat: bring it in with ` +
    `library_use, read it, and tell me what it does and which slots it needs before changing anything.`
  )
}

export async function deleteItem(client: AgentdClient, item: LibraryItem): Promise<void> {
  const index = await readIndex(client)
  await remove(client, `${LIBRARY_DIR}/${item.path}`)
  index.items = index.items.filter((i) => i.id !== item.id)
  await writeIndex(client, index)
}

export async function updateNote(client: AgentdClient, item: LibraryItem, note: string): Promise<void> {
  const index = await readIndex(client)
  const it = index.items.find((i) => i.id === item.id)
  if (!it) return
  it.note = note.trim()
  await writeIndex(client, index)
}

// ---------------------------------------------------------------------------- the tool's copies

/** The copies `library_use` asked for, out of its `details` — a Library reference into a slot.
 *  Shape-checked, never trusted: an older plugin sends no details, which must read as nothing. */
export function libraryCopies(details: unknown): Array<{ from: string; to: string }> {
  const d = details as { copy?: unknown } | null | undefined
  if (!d || !Array.isArray(d.copy)) return []
  return d.copy
    .map((c) => ({ from: String((c as { from?: unknown })?.from || '').trim(), to: String((c as { to?: unknown })?.to || '').trim() }))
    .filter((c) => c.from.startsWith(`${LIBRARY_DIR}/`) && c.to && !c.to.includes('..'))
}

/** The HOST half of `library_use` for a reference: the sandbox has no media, so the tool says
 *  what it wants copied and this does it — the same division as comfy_delete. */
export async function applyLibraryCopies(
  client: AgentdClient,
  copies: Array<{ from: string; to: string }>,
): Promise<number> {
  let done = 0
  for (const c of copies) {
    try {
      await copy(client, c.from, c.to, true)
      done += 1
    } catch (e) {
      console.error('[library] refused to copy %s → %s: %s', c.from, c.to, String((e as Error)?.message || e))
    }
  }
  return done
}

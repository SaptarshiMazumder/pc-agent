/* Templates — a whole chat's setup, kept as one reusable thing.
 *
 * WHAT A TEMPLATE IS. Every workflow a chat built (run file, ComfyUI file, installer), in the
 * order they were built, plus the inputs the person fills (the chat's reference slots, with what
 * each is for) and a thumbnail. One folder in the Library:
 *
 *     library/<saved|uploaded>/templates/<slug>/
 *         template.json            the manifest below — the contract
 *         workflows/<role>.api.json, <role>.json, install_<role>.py, install_<role>.manifest.json
 *         setup.json               the SETUP GUIDE: every node pack and model, with its link
 *         about.json               the ABOUT: what it does, in full (template-about.ts)
 *                                  (template-setup-guide.ts) — what template_setup installs from
 *         thumb.<ext>              optional
 *
 * THE MANIFEST IS THE CONTRACT, not this code. A downloaded template is that folder zipped; an
 * uploaded one is checked against the manifest and unpacked into `uploaded/templates/`; and the
 * app's own suggested templates are the same folders, shipped. Everything that USES a template
 * (the Library card, the agent's `template_use`) reads only `template.json` and the files it
 * names, so a template from any of those three places behaves identically.
 *
 * THE WINDOW WRITES, THE AGENT READS — the Library's rule (library.ts). The agent brings a
 * template into a chat with `template_use`; it never writes one.
 */

import { unzipSync, zipSync, strFromU8, strToU8 } from 'fflate'

import type { AgentdClient } from '@agentd/client'

import { fileUrl, type Artifact } from './artifacts'
import { TEMPLATE_ABOUT, type TemplateAbout } from './template-about'
import { TEMPLATE_SETUP, type SetupGuide } from './template-setup-guide'
import {
  copy,
  KIND_DIR,
  LIBRARY_DIR,
  listArtifacts,
  newId,
  nowIso,
  readIndex,
  slotsInGraph,
  slug,
  splitExt,
  upload,
  utf8Base64,
  writeIndex,
  type LibraryIndex,
  type LibraryItem,
} from './library'

export const TEMPLATE_FORMAT = 'comfy-penguin-template'
/** What "Save as template" writes. Version 2 (Comfy Cloud, one `guide.json` the agent runs from)
 *  is read here and written by hand for now; the save writes it once it can build a guide. */
export const TEMPLATE_VERSION = 1
/** The newest manifest this window reads. */
export const TEMPLATE_VERSION_READ = 2
/** A version-2 template's runbook for the agent: models with links, the settings it may change,
 *  how to run it (plugins/comfy-bridge/template_guide.py). */
export const TEMPLATE_GUIDE = 'guide.json'
export const TEMPLATE_MANIFEST = 'template.json'
const WORKFLOWS_DIR = 'workflows'

/** One input the person fills to run the template: a reference slot and what it is for. */
export interface TemplateInput {
  role: string
  what: string
}

/** One workflow of the template, in run order. Paths are relative to the template folder. */
export interface TemplateStep {
  role: string
  api: string
  ui?: string
  installer: string[]
  /** The slot roles this workflow's graph loads (`@role`). */
  slots: string[]
}

export interface TemplateManifest {
  format: typeof TEMPLATE_FORMAT
  version: number
  name: string
  description: string
  created: string
  from?: { chat: string; title: string }
  thumbnail?: string
  /** The setup guide's file (TEMPLATE_SETUP). Absent on a template saved before guides: the
   *  agent then works its sources out itself. */
  setup?: string
  /** The about's file (TEMPLATE_ABOUT). Absent on a template saved before abouts. */
  about?: string
  /** Version 2: the agent's runbook (TEMPLATE_GUIDE). */
  guide?: string
  inputs: TemplateInput[]
  steps: TemplateStep[]
}

/** One workflow of a chat, as the template save needs it. */
export interface ChatWorkflowFiles {
  role: string
  api: Artifact
  ui?: Artifact
  installer: Artifact[]
}

// ---------------------------------------------------------------------------- the manifest

/** A parsed manifest, or the reason it is not one. Used on every read, not only on upload: a
 *  hand-edited or foreign template must fail in words, never half-load. */
export function parseManifest(raw: unknown): TemplateManifest | string {
  const m = raw as Partial<TemplateManifest> | null
  if (!m || typeof m !== 'object') return 'template.json is not an object'
  if (m.format !== TEMPLATE_FORMAT) return `not a ${TEMPLATE_FORMAT} (format is ${String(m.format)})`
  if (Number(m.version) > TEMPLATE_VERSION_READ) return `made by a newer app (version ${m.version})`
  if (!m.name || !String(m.name).trim()) return 'the template has no name'
  if (!Array.isArray(m.steps) || !m.steps.length) return 'the template has no workflows'
  const steps: TemplateStep[] = []
  for (const s of m.steps) {
    const api = String((s as TemplateStep)?.api || '')
    if (!api.endsWith('.api.json') || !safeRel(api)) return `a step has no usable run file (${api || 'none'})`
    steps.push({
      role: String((s as TemplateStep).role || splitExt(api.split('/').pop() || '').stem),
      api,
      ui: (s as TemplateStep).ui && safeRel(String((s as TemplateStep).ui)) ? String((s as TemplateStep).ui) : undefined,
      installer: Array.isArray((s as TemplateStep).installer)
        ? (s as TemplateStep).installer.map(String).filter(safeRel)
        : [],
      slots: Array.isArray((s as TemplateStep).slots) ? (s as TemplateStep).slots.map(String) : [],
    })
  }
  return {
    format: TEMPLATE_FORMAT,
    version: Number(m.version) || TEMPLATE_VERSION,
    name: String(m.name).trim(),
    description: String(m.description || ''),
    created: String(m.created || ''),
    ...(m.from ? { from: { chat: String(m.from.chat || ''), title: String(m.from.title || '') } } : {}),
    ...(m.thumbnail && safeRel(String(m.thumbnail)) ? { thumbnail: String(m.thumbnail) } : {}),
    ...(m.setup === TEMPLATE_SETUP ? { setup: TEMPLATE_SETUP } : {}),
    ...(m.about === TEMPLATE_ABOUT ? { about: TEMPLATE_ABOUT } : {}),
    ...(m.guide === TEMPLATE_GUIDE ? { guide: TEMPLATE_GUIDE } : {}),
    inputs: Array.isArray(m.inputs)
      ? m.inputs
          .map((i) => ({ role: String(i?.role || '').replace(/^@/, '').trim(), what: String(i?.what || '') }))
          .filter((i) => i.role)
      : [],
    steps,
  }
}

/** Only `template.json`, `thumb.*` and `workflows/<file>` may be in a template — no `..`, no
 *  absolute path, nothing that would land outside its own folder when unpacked. */
function safeRel(rel: string): boolean {
  if (!rel || rel.startsWith('/') || rel.includes('\\') || rel.split('/').includes('..')) return false
  return rel === TEMPLATE_MANIFEST || rel === TEMPLATE_SETUP || rel === TEMPLATE_ABOUT || rel === TEMPLATE_GUIDE || /^thumb\.[a-z0-9]+$/i.test(rel) || /^workflows\/[^/]+\.(json|py)$/i.test(rel)
}

function templateDir(item: LibraryItem): string {
  return `${LIBRARY_DIR}/${item.path}`
}

/** A folder name no other template of this origin uses. */
function freeFolder(index: LibraryIndex, origin: 'saved' | 'uploaded', wanted: string): string {
  const taken = new Set(index.items.filter((i) => i.kind === 'template').map((i) => i.path.toLowerCase()))
  const base = `${origin}/${KIND_DIR.template}/`
  if (!taken.has(`${base}${wanted}`.toLowerCase())) return wanted
  for (let n = 2; ; n++) if (!taken.has(`${base}${wanted}-${n}`.toLowerCase())) return `${wanted}-${n}`
}

// ---------------------------------------------------------------------------- saving a chat

/** A chat's workflows in the order they were BUILT (oldest run file first): that is the order a
 *  pipeline of stills → video → upscale runs in, and the only order the chat itself recorded. */
export function inBuildOrder(workflows: ChatWorkflowFiles[]): ChatWorkflowFiles[] {
  // The input is NEWEST FIRST (collectWorkflows). Reversed before the stable sort, files with no
  // timestamp keep their build order; the first two templates saved came out video-first, which
  // runs a video before the images it is made from.
  return [...workflows].reverse().sort((a, b) => (a.api.modified || 0) - (b.api.modified || 0))
}

export async function saveChatAsTemplate(
  client: AgentdClient,
  args: {
    name: string
    description: string
    workflows: ChatWorkflowFiles[]
    inputs: TemplateInput[]
    thumbnail?: Artifact
    /** The setup guide, complete — the Save dialog does not save with a gap left open. */
    setup: SetupGuide
    /** The about, as the person left it in the dialog. */
    about: TemplateAbout
    /** Workspace-relative path of each chat file, which is what the daemon copies by. */
    relOf: (a: Artifact) => string
  },
  from: { chat: string; title: string },
): Promise<LibraryItem> {
  if (!args.workflows.length) throw new Error('this chat has no workflow to keep yet')
  const index = await readIndex(client)
  const folder = freeFolder(index, 'saved', slug(args.name))
  const path = `saved/${KIND_DIR.template}/${folder}`
  const dir = `${LIBRARY_DIR}/${path}`

  const steps: TemplateStep[] = []
  for (const wf of inBuildOrder(args.workflows)) {
    const put = async (a: Artifact): Promise<string> => {
      await copy(client, args.relOf(a), `${dir}/${WORKFLOWS_DIR}/${a.name}`, true)
      return `${WORKFLOWS_DIR}/${a.name}`
    }
    const api = await put(wf.api)
    const ui = wf.ui ? await put(wf.ui) : undefined
    const installer: string[] = []
    for (const f of wf.installer) installer.push(await put(f))
    const text = await (await fetch(fileUrl(wf.api.path), { cache: 'no-store' })).text()
    steps.push({ role: wf.role, api, ...(ui ? { ui } : {}), installer, slots: slotsInGraph(text) })
  }

  let thumbnail: string | undefined
  if (args.thumbnail) {
    thumbnail = `thumb${splitExt(args.thumbnail.name).ext || '.png'}`
    await copy(client, args.relOf(args.thumbnail), `${dir}/${thumbnail}`, true)
  }

  await upload(client, dir, TEMPLATE_SETUP, utf8Base64(JSON.stringify(args.setup, null, 2) + '\n'), true)
  await upload(client, dir, TEMPLATE_ABOUT, utf8Base64(JSON.stringify(args.about, null, 2) + '\n'), true)

  const at = nowIso()
  const manifest: TemplateManifest = {
    format: TEMPLATE_FORMAT,
    version: TEMPLATE_VERSION,
    name: args.name.trim(),
    description: args.description.trim(),
    created: at,
    from,
    ...(thumbnail ? { thumbnail } : {}),
    setup: TEMPLATE_SETUP,
    about: TEMPLATE_ABOUT,
    inputs: args.inputs,
    steps,
  }
  await upload(client, dir, TEMPLATE_MANIFEST, utf8Base64(JSON.stringify(manifest, null, 2) + '\n'), true)

  const item: LibraryItem = {
    id: newId('template'),
    kind: 'template',
    origin: 'saved',
    name: manifest.name,
    note: manifest.description,
    path,
    versions: [],
    from,
    created: at,
  }
  index.items.push(item)
  await writeIndex(client, index)
  return item
}

// ---------------------------------------------------------------------------- reading

/** Every file of a template, keyed by its path inside the template folder. */
export async function templateFiles(client: AgentdClient, item: LibraryItem): Promise<Map<string, Artifact>> {
  const out = new Map<string, Artifact>()
  const dir = templateDir(item)
  for (const a of await listArtifacts(client, dir)) out.set(a.name, a)
  for (const a of await listArtifacts(client, `${dir}/${WORKFLOWS_DIR}`)) out.set(`${WORKFLOWS_DIR}/${a.name}`, a)
  return out
}

export async function readTemplate(
  client: AgentdClient,
  item: LibraryItem,
): Promise<{ manifest: TemplateManifest; files: Map<string, Artifact> }> {
  const files = await templateFiles(client, item)
  const m = files.get(TEMPLATE_MANIFEST)
  if (!m) throw new Error(`${item.name} has no ${TEMPLATE_MANIFEST}`)
  const res = await fetch(fileUrl(m.path), { cache: 'no-store' })
  if (!res.ok) throw new Error(`could not read ${item.name} (HTTP ${res.status})`)
  const manifest = parseManifest(await res.json())
  if (typeof manifest === 'string') throw new Error(`${item.name}: ${manifest}`)
  return { manifest, files }
}

// ---------------------------------------------------------------------------- download / upload

/** The template as one `<slug>.template.zip`, built in the browser from its files. */
export async function downloadTemplate(client: AgentdClient, item: LibraryItem): Promise<void> {
  const { files } = await readTemplate(client, item)
  const folder = item.path.split('/').pop() || slug(item.name)
  const entries: Record<string, Uint8Array> = {}
  for (const [rel, a] of files) {
    if (!safeRel(rel)) continue
    const res = await fetch(fileUrl(a.path), { cache: 'no-store' })
    if (!res.ok) throw new Error(`could not read ${rel} (HTTP ${res.status})`)
    entries[`${folder}/${rel}`] = new Uint8Array(await res.arrayBuffer())
  }
  const blob = new Blob([zipSync(entries)], { type: 'application/zip' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${folder}.template.zip`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

function bytesBase64(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

/** A downloaded template back into the Library, under `uploaded/templates/`. The manifest is
 *  checked and every file it names must be in the zip; anything else in the zip is ignored. */
export async function uploadTemplate(client: AgentdClient, file: File): Promise<LibraryItem> {
  let zipped: Record<string, Uint8Array>
  try {
    zipped = unzipSync(new Uint8Array(await file.arrayBuffer()))
  } catch {
    throw new Error(`${file.name} is not a zip file`)
  }
  // The manifest may sit at the root or one folder down (a downloaded template is `<slug>/…`).
  const manifestPath = Object.keys(zipped)
    .filter((p) => p === TEMPLATE_MANIFEST || p.endsWith(`/${TEMPLATE_MANIFEST}`))
    .sort((a, b) => a.length - b.length)[0]
  if (!manifestPath) throw new Error(`${file.name} has no ${TEMPLATE_MANIFEST} — it is not a template`)
  const prefix = manifestPath.slice(0, -TEMPLATE_MANIFEST.length)
  let parsed: unknown
  try {
    parsed = JSON.parse(strFromU8(zipped[manifestPath]))
  } catch {
    throw new Error(`${TEMPLATE_MANIFEST} in ${file.name} is not valid JSON`)
  }
  const manifest = parseManifest(parsed)
  if (typeof manifest === 'string') throw new Error(`${file.name}: ${manifest}`)
  const missing = manifestFiles(manifest).filter((rel) => !zipped[`${prefix}${rel}`])
  if (missing.length) throw new Error(`${file.name} is missing ${missing.join(', ')}`)

  const index = await readIndex(client)
  const folder = freeFolder(index, 'uploaded', slug(manifest.name))
  const path = `uploaded/${KIND_DIR.template}/${folder}`
  await writeTemplateFiles(client, `${LIBRARY_DIR}/${path}`, manifest, (rel) => zipped[`${prefix}${rel}`])

  const item: LibraryItem = {
    id: newId('template'),
    kind: 'template',
    origin: 'uploaded',
    name: manifest.name,
    note: manifest.description,
    path,
    versions: [],
    created: nowIso(),
  }
  index.items.push(item)
  await writeIndex(client, index)
  return item
}

/** Every file a manifest names, and the manifest itself, into `dir`. The one writer for a
 *  template's folder, whether its bytes came from a zip or from the app's own shipped copy. */
async function writeTemplateFiles(
  client: AgentdClient,
  dir: string,
  manifest: TemplateManifest,
  bytesOf: (rel: string) => Uint8Array,
): Promise<void> {
  for (const rel of manifestFiles(manifest)) {
    const parts = rel.split('/')
    const name = parts.pop() as string
    await upload(client, [dir, ...parts].join('/'), name, bytesBase64(bytesOf(rel)), true)
  }
  await upload(client, dir, TEMPLATE_MANIFEST, bytesBase64(strToU8(JSON.stringify(manifest, null, 2) + '\n')), true)
}

/** The files a manifest names, besides itself. */
function manifestFiles(manifest: TemplateManifest): string[] {
  return [
    ...manifest.steps.flatMap((s) => [s.api, ...(s.ui ? [s.ui] : []), ...s.installer]),
    ...(manifest.thumbnail ? [manifest.thumbnail] : []),
    ...(manifest.setup ? [manifest.setup] : []),
    ...(manifest.about ? [manifest.about] : []),
    ...(manifest.guide ? [manifest.guide] : []),
  ]
}

// ---------------------------------------------------------------------------- suggested

/** Where the app's own templates are served from: its static files, beside the marketing media. */
const SUGGESTED_BASE = 'suggested-templates'

/** A "what goes in → what comes out" row, as the landing page draws it. */
export interface ShowcaseStep {
  src: string
  label: string
  kind: 'input' | 'image' | 'video'
}

/** One card on the Templates page: which shipped template, and how it is shown. */
export interface SuggestedTemplate {
  slug: string
  title: string
  prompt: string
  /** null until its media exists: the card then shows the template's thumbnail instead. */
  showcase: { inputs: ShowcaseStep[]; outputs: ShowcaseStep[] } | null
}

/** The Templates page's catalogue (suggested-templates/index.json). */
export async function readSuggestedCatalogue(): Promise<SuggestedTemplate[]> {
  const res = await fetch(`${SUGGESTED_BASE}/index.json`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`could not load the templates (HTTP ${res.status})`)
  const data = (await res.json()) as { templates?: SuggestedTemplate[] }
  return Array.isArray(data.templates) ? data.templates.filter((t) => t && t.slug && t.title) : []
}

/** A shipped template's manifest, read from the app's own files. */
export async function readSuggestedManifest(slugName: string): Promise<TemplateManifest> {
  const res = await fetch(`${SUGGESTED_BASE}/${slugName}/${TEMPLATE_MANIFEST}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`could not load template ${slugName} (HTTP ${res.status})`)
  const manifest = parseManifest(await res.json())
  if (typeof manifest === 'string') throw new Error(`${slugName}: ${manifest}`)
  return manifest
}

/** The shipped template in this person's Library, installed on first use and refreshed when the
 *  app ships a newer copy. It becomes an ordinary Library template (origin `suggested`), so the
 *  agent's `template_use` reads it like any other — there is no second path. */
export async function installSuggestedTemplate(client: AgentdClient, entry: SuggestedTemplate): Promise<LibraryItem> {
  const manifest = await readSuggestedManifest(entry.slug)
  const index = await readIndex(client)
  const path = `suggested/${KIND_DIR.template}/${slug(entry.slug)}`
  // THE SHIPPED COPY'S STAMP (`created`) says whether what is installed is current.
  const stamp = manifest.created || manifest.name
  let item = index.items.find((i) => i.kind === 'template' && i.origin === 'suggested' && i.path === path)
  if (item && item.versions[0]?.at === stamp) return item

  const bytes = new Map<string, Uint8Array>()
  for (const rel of manifestFiles(manifest)) {
    const res = await fetch(`${SUGGESTED_BASE}/${entry.slug}/${rel}`, { cache: 'no-store' })
    if (!res.ok) throw new Error(`template ${entry.slug} is missing ${rel} (HTTP ${res.status})`)
    bytes.set(rel, new Uint8Array(await res.arrayBuffer()))
  }
  await writeTemplateFiles(client, `${LIBRARY_DIR}/${path}`, manifest, (rel) => bytes.get(rel) as Uint8Array)

  if (!item) {
    item = {
      id: newId('template'),
      kind: 'template',
      origin: 'suggested',
      name: entry.title,
      note: manifest.description,
      path,
      versions: [],
      created: nowIso(),
    }
    index.items.push(item)
  }
  item.name = entry.title
  item.note = manifest.description
  item.versions = [{ v: 1, at: stamp }]
  await writeIndex(client, index)
  return item
}

// ---------------------------------------------------------------------------- using

/** What a new chat is told when the person presses "Use this template". The agent brings it in
 *  with `template_use`, which copies every workflow and declares the inputs, then explains. */
export function useTemplateMessage(item: LibraryItem): string {
  return (
    `Use my template "${item.name}" (id ${item.id}). Tell me briefly what it makes, which inputs ` +
    `to add, and what a run costs.`
  )
}

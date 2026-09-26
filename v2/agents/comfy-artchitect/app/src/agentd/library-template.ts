/* Templates — a whole chat's setup, kept as one reusable thing.
 *
 * WHAT A TEMPLATE IS. Every workflow a chat built (run file, ComfyUI file, installer), in the
 * order they were built, plus the inputs the person fills (the chat's reference slots, with what
 * each is for) and a thumbnail. One folder in the Library:
 *
 *     library/<saved|uploaded>/templates/<slug>/
 *         template.json            the manifest below — the contract
 *         workflows/<role>.api.json, <role>.json, install_<role>.py, install_<role>.manifest.json
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
export const TEMPLATE_VERSION = 1
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
  if (Number(m.version) > TEMPLATE_VERSION) return `made by a newer app (version ${m.version})`
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
  return rel === TEMPLATE_MANIFEST || /^thumb\.[a-z0-9]+$/i.test(rel) || /^workflows\/[^/]+\.(json|py)$/i.test(rel)
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
  return [...workflows].sort((a, b) => (a.api.modified || 0) - (b.api.modified || 0))
}

export async function saveChatAsTemplate(
  client: AgentdClient,
  args: {
    name: string
    description: string
    workflows: ChatWorkflowFiles[]
    inputs: TemplateInput[]
    thumbnail?: Artifact
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

  const at = nowIso()
  const manifest: TemplateManifest = {
    format: TEMPLATE_FORMAT,
    version: TEMPLATE_VERSION,
    name: args.name.trim(),
    description: args.description.trim(),
    created: at,
    from,
    ...(thumbnail ? { thumbnail } : {}),
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
  const named = [
    ...manifest.steps.flatMap((s) => [s.api, ...(s.ui ? [s.ui] : []), ...s.installer]),
    ...(manifest.thumbnail ? [manifest.thumbnail] : []),
  ]
  const missing = named.filter((rel) => !zipped[`${prefix}${rel}`])
  if (missing.length) throw new Error(`${file.name} is missing ${missing.join(', ')}`)

  const index = await readIndex(client)
  const folder = freeFolder(index, 'uploaded', slug(manifest.name))
  const path = `uploaded/${KIND_DIR.template}/${folder}`
  const dir = `${LIBRARY_DIR}/${path}`
  for (const rel of named) {
    const parts = rel.split('/')
    const name = parts.pop() as string
    await upload(client, [dir, ...parts].join('/'), name, bytesBase64(zipped[`${prefix}${rel}`]), true)
  }
  await upload(client, dir, TEMPLATE_MANIFEST, bytesBase64(strToU8(JSON.stringify(manifest, null, 2) + '\n')), true)

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

// ---------------------------------------------------------------------------- using

/** What a new chat is told when the person presses "Use this template". The agent brings it in
 *  with `template_use`, which copies every workflow and declares the inputs, then explains. */
export function useTemplateMessage(item: LibraryItem): string {
  return (
    `Use my template "${item.name}" (id ${item.id}): bring it in with template_use and set up its inputs. ` +
    `Then tell me briefly what it does and which inputs to add on the Inputs tab to run it as it is — ` +
    `or ask me what I want to change.`
  )
}

/* Instagram posts, read and changed through the agent's post tools (tools.invoke): collections of
 * generations gathered from any campaign, the posts made from them, the brand they carry.
 *
 * Changing a collection, a post's plan or the brand is free and instant — called directly.
 * Planning (the agent's model) and rendering (ffmpeg, can take a minute) are chat messages
 * carrying the exact call, like every other long action. */

import type { AgentdClient } from '@agentd/client'

import { fileUrl } from './artifacts'
import type { Media } from './campaigns'
import { readFile } from './chat'
import { AGENT_ID } from './client'

export interface CollectionItem {
  path: string
  kind: 'image' | 'video'
  campaign: string
  product: string
  note: string
}

/** A product in a collection and where it was found — what the caption credits. */
export interface ProductSource {
  name: string
  found_at: string
  link: string
  price: string
}

export interface Collection {
  slug: string
  name: string
  items: CollectionItem[]
  created: number
  products: ProductSource[]
}

export interface TextCue {
  text: string
  y: number
  face: 'heading' | 'label'
  size: number
  align: 'left' | 'center' | 'right'
  start: number
  end: number
  fade_in: number
  fade_out: number
  to_y: number
  move_at: number
  move_s: number
  color: string
}

export interface ClipEdit {
  speed: number
  trim_start: number
  trim_end: number
  fade_in: number
  fade_out: number
}

export interface Slide {
  item: string
  kind: 'image' | 'video'
  cues: TextCue[]
  edit: ClipEdit
  seconds: number
  note: string
  /** Its design (HTML, workspace path) and the still of it last rendered; '' = a plain slide. */
  design: string
  preview: string
  /** The design reference its design follows (slug); '' = none. */
  reference?: string
}

/** A slide design to start from — built in, or saved from a slide the user liked. */
export interface DesignTemplate {
  slug: string
  name: string
  description: string
  kind: 'still' | 'video'
  thumb: string
  tags: string[]
  origin: 'builtin' | 'saved'
}

export async function listTemplates(client: AgentdClient): Promise<{ templates: DesignTemplate[]; media: Media }> {
  const d = await invoke(client, 'design_template_list', {})
  return { templates: (d.templates || []) as DesignTemplate[], media: mediaFor(String(d.root || '')) }
}

/** A picture of a design worth following, with what a designer read off it. */
export interface DesignReference {
  slug: string
  name: string
  image: string
  source: string
  suits: string[]
  spec: Partial<Record<'layout' | 'photos' | 'type' | 'palette' | 'decoration' | 'text_slots' | 'mood', string>>
  created: number
}

export async function listReferences(client: AgentdClient): Promise<{ references: DesignReference[]; media: Media }> {
  const d = await invoke(client, 'design_reference_list', {})
  return { references: (d.references || []) as DesignReference[], media: mediaFor(String(d.root || '')) }
}

/** Upload a screenshot of a design and keep it as a reference (the agent's model reads it — a few seconds). */
export async function addReference(client: AgentdClient, file: File, name: string, notes: string): Promise<DesignReference> {
  if (!/\.(png|jpe?g|webp)$/i.test(file.name)) throw new Error(`${file.name}: use an image (png, jpg, webp)`)
  const read = await readFile(file)
  const res: any = await client.request('workspace.upload', { agentId: AGENT_ID, path: 'uploads', name: file.name, dataBase64: read.dataBase64 })
  if (!res?.ok) throw new Error(`${file.name}: ${String(res?.error || 'upload failed')}`)
  const d = await invoke(client, 'design_reference_save', { image: String(res.path), name, notes, source: 'your screenshot' })
  return d.reference as DesignReference
}

export async function deleteReference(client: AgentdClient, slug: string): Promise<void> {
  await invoke(client, 'design_reference_delete', { reference: slug })
}

/** What the design follows: KEEP = the references the post already follows (or the library's best);
 *  NONE = no reference; else one reference's slug. */
export const KEEP_REFERENCES = ''
export const NO_REFERENCE = '-'

/** The message that designs slides (all, or the numbers given) — a line a person reads, then the exact call. */
export const designCommand = (post: Post, slides: number[], notes: string, template: string, reference = KEEP_REFERENCES) => {
  const args: Record<string, unknown> = { post: post.slug }
  if (slides.length) args.slides = slides
  if (notes.trim()) args.notes = notes.trim()
  if (template) args.template = template
  if (reference === NO_REFERENCE) args.references = []
  else if (reference) args.references = [reference]
  const what = slides.length ? `slide${slides.length > 1 ? 's' : ''} ${slides.join(', ')}` : 'all slides'
  return `${post.name} · design ${what}${notes.trim() ? `: ${notes.trim()}` : ''}\npost_design ${JSON.stringify(args)}`
}

export interface Post {
  slug: string
  name: string
  collection: string
  format: 'carousel' | 'reel'
  slides: Slide[]
  caption: string
  hashtags: string[]
  session: string
  rendered: string[]
  rendered_at: number
  /** Who made the files: our renderer, or a Canva template the agent filled in the browser. */
  design?: 'renderer' | 'canva'
  canva_url?: string
}

export interface Brand {
  name: string
  handle: string
  tagline: string
  voice: string
  /** The line every caption ends with — "Independently curated. Not sponsored. …" */
  caption_disclosure: string
  heading_font: string
  label_font: string
  text_color: string
  accent_color: string
  /** The brand's design rules, taught by the user — every design and review follows them. */
  design_notes: string[]
}

export const HEADING_FONTS = ['cormorant', 'playfair', 'oswald', 'inter']
export const LABEL_FONTS = ['manrope', 'manrope-bold', 'inter']

async function invoke(client: AgentdClient, name: string, params: Record<string, unknown>): Promise<any> {
  const res: any = await client.request('tools.invoke', { name, params })
  if (!res?.details) throw new Error(res?.text || `${name} returned no data`)
  return res.details
}

function mediaFor(root: string): Media {
  const base = root.replace(/[\\/]+$/, '')
  return (rel) => (rel ? fileUrl(/^[a-zA-Z]:[\\/]|^\//.test(rel) ? rel : `${base}/${rel}`) : '')
}

export async function listCollections(client: AgentdClient): Promise<{ collections: Collection[]; media: Media }> {
  const d = await invoke(client, 'collection_list', {})
  return { collections: (d.collections || []) as Collection[], media: mediaFor(String(d.root || '')) }
}

/** Selected generations of one campaign into a collection — an existing one, or a new one (`isNew`). */
export async function addToCollection(
  client: AgentdClient,
  collection: string,
  isNew: boolean,
  items: { path: string; campaign: string }[],
): Promise<Collection> {
  const d = await invoke(client, 'collection_add', { collection, new: isNew, items })
  return d.collection as Collection
}

/** The user's own images and clips (made elsewhere): uploaded, then copied into the collection,
 *  each under the product it shows. */
export async function importToCollection(
  client: AgentdClient,
  collection: string,
  isNew: boolean,
  files: File[],
  product: string,
): Promise<Collection> {
  const uploaded = []
  for (const file of files) {
    if (!/\.(png|jpe?g|webp|mp4|mov|webm)$/i.test(file.name)) throw new Error(`${file.name}: use an image (png, jpg, webp) or a clip (mp4, mov, webm)`)
    const read = await readFile(file)
    const res: any = await client.request('workspace.upload', { agentId: AGENT_ID, path: 'uploads', name: file.name, dataBase64: read.dataBase64 })
    if (!res?.ok) throw new Error(`${file.name}: ${String(res?.error || 'upload failed')}`)
    uploaded.push({ path: String(res.path), product })
  }
  const d = await invoke(client, 'collection_import', { collection, new: isNew, files: uploaded })
  return d.collection as Collection
}

export async function updateCollection(
  client: AgentdClient,
  collection: string,
  action: 'remove' | 'reorder' | 'rename' | 'delete' | 'product',
  extra: { paths?: string[]; name?: string; product?: ProductSource } = {},
): Promise<void> {
  await invoke(client, 'collection_update', { collection, action, ...extra })
}

/** One slide of a design run: where it is, its latest preview, what the art director still wants fixed. */
export interface SlideDesignState {
  state: 'waiting' | 'designing' | 'reviewing' | 'done' | 'failed' | 'stopped'
  round: number
  rounds: number
  preview: string
  problems: string[]
  reference: string
}

/** A post's last design run, written as it moves (times in seconds). */
export interface DesignProgress {
  post: string
  active: boolean
  started: number
  updated: number
  slides: Record<string, SlideDesignState>
}

export async function listPosts(
  client: AgentdClient,
  filter: { session?: string; post?: string } = {},
): Promise<{ posts: Post[]; media: Media; designing: Record<string, DesignProgress> }> {
  const d = await invoke(client, 'post_list', filter)
  return { posts: (d.posts || []) as Post[], media: mediaFor(String(d.root || '')), designing: (d.designing || {}) as Record<string, DesignProgress> }
}

export async function updatePost(client: AgentdClient, post: string, changes: Partial<Pick<Post, 'slides' | 'caption' | 'hashtags' | 'format' | 'name'>>): Promise<Post> {
  const d = await invoke(client, 'post_update', { post, ...changes })
  return d.post as Post
}

export async function getBrand(client: AgentdClient): Promise<Brand> {
  return (await invoke(client, 'brand_profile', { action: 'get' })).brand as Brand
}

export async function setBrand(client: AgentdClient, changes: Partial<Brand>): Promise<Brand> {
  return (await invoke(client, 'brand_profile', { action: 'set', ...changes })).brand as Brand
}

/** Open Canva in the agent's browser window — where the user signs in once (a free account is
 *  fine) and later watches the agent design. Quick, so called directly. */
export async function openCanva(client: AgentdClient): Promise<void> {
  const res: any = await client.request('tools.invoke', { name: 'browser', params: { action: 'navigate', url: 'https://www.canva.com' } })
  if (res?.isError || res?.is_error) throw new Error(String(res?.text || 'the browser could not open'))
}

/** The message asking the agent to give a planned post a Canva template's look. */
export const canvaCommand = (post: Post) =>
  `Design the post ${post.slug} in Canva: pick a free Instagram ${post.format === 'reel' ? 'Reel' : 'post'} template that suits it, fill it with the plan's slides and words, download it and attach it.`

/** The sentence a collection card puts in the composer (AGENTS.md, "Instagram posts"). */
export const POST_START = (name: string) => `Make an Instagram post from the collection ${name}.`

/** The message that renders a post: a line a person reads, then the exact call. */
export const renderCommand = (post: Post) => `${post.name} · render the post\npost_render ${JSON.stringify({ post: post.slug })}`

/* Ad Studio's campaigns, as the window sees them.
 *
 * READ through the agent's own tools, called with `tools.invoke` (no model turn, nothing paid):
 * campaign_list, campaign_status, campaign_generations, cast_list, generation_models. Their
 * `details` carry the data; the text is for people and never parsed.
 *
 * TWO WAYS TO ACT. Picking a step's result is free and instant: `pickResult` calls step_pick
 * directly. Anything that generates (and costs) is a chat message carrying the exact tool call
 * (`command`), which the agent runs as written — so the user's click is the approval, and the
 * thread shows what was asked for.
 */

import type { AgentdClient } from '@agentd/client'

import { fileUrl } from './artifacts'
import { readFile } from './chat'
import { AGENT_ID } from './client'

export type StepAction = 'brief' | 'sheet' | 'product_sheet' | 'images' | 'video'

/** One thing a step made, with the checker's advice. */
export interface StepResult {
  path: string
  kind: 'image' | 'video' | string
  provider: string
  model: string
  cost_usd: number
  credits: number | null
  made_at: number
  score: number | null
  passed: boolean | null
  /** a text ad: every word of its copy rendered exactly; false = a word is wrong. */
  text_exact?: boolean | null
  problems: string[]
  /** video: the image it started from. */
  first_frame: string
  /** an image fix: the image it fixed. */
  fix_of: string
  /** a clip edit or extension: the clip it came from. */
  from_clip: string
  /** the user's own image, uploaded — not generated, not checked. */
  uploaded?: boolean
  /** a text ad whose words are set in real fonts: they can be edited (poster_text). */
  editable?: boolean
}

export interface CampaignStep {
  id: string
  title: string
  action: StepAction
  status: 'todo' | 'done' | 'skipped'
  scene: string
  /** video: the step whose pick is its first frame. */
  source: string
  prompt: string
  references: string[]
  cast: boolean
  shows_product: boolean
  count: number
  pick: string
  /** What it was last run with ("provider/model", seconds, resolution). */
  model: string
  seconds: number
  resolution: string
  /** What a run without changes would send — shown pre-filled in the Generate panel. */
  defaults: { prompt: string; references: string[]; seconds: number; panels?: string[] }
  /** Newest first. */
  results: StepResult[]
  /** video: its pick was made from an image its source step no longer picks. */
  stale: boolean
  /** A run the agent proposed and the user has not approved: shown pre-filled; {} = none. */
  proposal: { tool?: CommandTool; args?: Record<string, any> }
  /** A text ad's designs: 'overlay' = words set in real fonts (editable); '' = drawn by the model. */
  text?: '' | 'overlay'
}

export interface BriefShot {
  id: string
  purpose: string
  keyframe_prompt: string
  motion_prompt: string
  duration_s: number
  cast: string[]
  spec: Record<string, string>
}

export interface Brief {
  format_key: string
  concept: string
  hook: string
  caption: string
  look: Record<string, string>
  shots: BriefShot[]
  /** a text ad's words (headline, subline, offer, cta, fine_print); empty for a photo ad. */
  copy?: Record<string, string>
}

export interface CastMember {
  name: string
  description: string
  sheet: string
}

export interface CampaignDetail {
  campaign_id: string
  recipe_key: string
  recipe_title: string
  product: { name: string; category: string; photos: string[] }
  cast: CastMember | null
  brief: Brief | null
  direction: Record<string, string>
  session: string
  budget_usd: number
  spent_usd: number
  updated: number
  /** The first step not done yet ("" = all done) — a highlight, never a lock. */
  current: string
  /** "ask": every run needs the user's click (which approves its model); "auto": the agent runs it. */
  approval: 'ask' | 'auto'
  steps: CampaignStep[]
}

export interface GenModel {
  id: string
  provider: string
  model: string
  label: string
  references: boolean
  price: string
  /** How many reference images it takes; null = no stated limit. */
  max_references: number | null
  /** The clip lengths it can make, seconds; null = only its default. */
  durations: { min: number; max: number } | null
  /** Clips: the resolutions it makes, each with its price note; one entry = the only one. */
  resolutions?: Record<string, string> | null
  /** An edit mode of a model: for fixing an image, not for making one. */
  fix_only: boolean
  /** A clip model that only edits clips, never makes one. */
  edit_only?: boolean
  /** What it does to a selected clip natively, with that mode's price; null = not natively. */
  edit?: { price: string } | null
  extend?: { price: string } | null
}

export interface ModelList {
  default: string
  models: GenModel[]
}

export interface ModelLists {
  image: ModelList
  video: ModelList
  fix: ModelList
  /** Models that edit a selected clip. */
  edit: ModelList
  /** Models that extend a selected clip: natively (`extend` set) or by a new clip from its last frame. */
  extend: ModelList
}

export interface CampaignRow {
  id: string
  name: string
  /** The step it is on, or "done". */
  step: string
  recipe: string
  cast: string
  spent_usd: number
  updated: number
  mine: boolean
  cover: string
  stills: number
  clips: number
}

/** One thing a campaign paid for. */
export interface Generation extends StepResult {
  step: string
  step_title: string
  in_use: boolean
}

/** A workspace path as a URL the window can load. The tools answer with workspace-relative
 *  paths (what the agent reads and writes) plus the workspace `root`; the daemon's /file wants
 *  an absolute path. */
export type Media = (rel: string) => string

function mediaFor(root: string): Media {
  const base = root.replace(/[\\/]+$/, '')
  return (rel) => (rel ? fileUrl(/^[a-zA-Z]:[\\/]|^\//.test(rel) ? rel : `${base}/${rel}`) : '')
}

async function invoke(client: AgentdClient, name: string, params: Record<string, unknown>): Promise<any> {
  const res: any = await client.request('tools.invoke', { name, params })
  if (!res?.details) throw new Error(res?.text || `${name} returned no data`)
  return res.details
}

export async function listCampaigns(client: AgentdClient, session: string): Promise<{ rows: CampaignRow[]; media: Media }> {
  const d = await invoke(client, 'campaign_list', { session })
  return { rows: (d.campaigns || []) as CampaignRow[], media: mediaFor(String(d.root || '')) }
}

export async function campaignStatus(client: AgentdClient, campaign: string): Promise<{ campaign: CampaignDetail; media: Media }> {
  const d = await invoke(client, 'campaign_status', { campaign })
  return { campaign: d.campaign as CampaignDetail, media: mediaFor(String(d.root || '')) }
}

/** A cast member the agent proposed; nothing is made until the user approves it in the studio. */
export interface CastProposal {
  name: string
  description: string
  references: string[]
  /** provider/model the agent would use. */
  model: string
}

export async function listCast(client: AgentdClient): Promise<{ cast: CastMember[]; proposed: CastProposal[]; media: Media }> {
  const d = await invoke(client, 'cast_list', {})
  return {
    cast: (d.cast || []) as CastMember[],
    proposed: (d.proposed || []) as CastProposal[],
    media: mediaFor(String(d.root || '')),
  }
}

/** The user's click: make this proposed cast member on this model, once. -> the approval token. */
export async function approveCast(client: AgentdClient, name: string, model: string): Promise<string> {
  const d = await invoke(client, 'cast_approval', { name, action: 'approve', model })
  return String(d.token || '')
}

export async function dismissCast(client: AgentdClient, name: string): Promise<void> {
  await invoke(client, 'cast_approval', { name, action: 'dismiss' })
}

/** A recipe an ad can start from: what it is for, its default steps, whether it casts a model. */
export interface Recipe {
  key: string
  title: string
  covers: string
  steps: { id: string; title: string; action: StepAction }[]
  needs_cast: boolean
  variants: number
  /** Scenes to offer when it is chosen (product stills: "In an open gift box, from above"…). */
  scene_options: string[]
  /** 'poster' = a text ad: its brief is copy and design. */
  brief?: 'scene' | 'poster'
  /** Added to the start sentence: blanks for what the user should say (a text ad's copy). */
  start_text?: string
}

export async function listRecipes(client: AgentdClient): Promise<Recipe[]> {
  const d = await invoke(client, 'recipe_list', {})
  return (d.recipes || []) as Recipe[]
}

/* ── starting an ad: what the buttons put in the composer ─────────────────────────────────────
   Plain sentences the agent maps onto campaign_start / cast_create (AGENTS.md, "Starting from the
   window"). The first choice STARTS the message; the second is appended to it. */

export const START = {
  recipe: (key: string, extra = '') => `Make an ad for this product with the recipe ${key}.${extra ? ' ' + extra : ''}`,
  cast: (name: string) => `Make an ad for this product with ${name}.`,
  addRecipe: (key: string, extra = '') => `Use the recipe ${key}.${extra ? ' ' + extra : ''}`,
  addCast: (name: string) => `Use the cast member ${name}.`,
  scene: (scene: string) => `Scene: ${scene}.`,
  // ALWAYS SAYS AI-GENERATED: the window's New cast member is for AI faces only, so the agent
  // never has to ask. The name comes last, where the cursor lands.
  newCast: 'Make a new cast member for it from the attached face image — it is AI-generated, not a real person; keep her face exactly. Her name: ',
  newCastAlone: 'Create a new cast member from the attached face image — it is AI-generated, not a real person; keep her face exactly. Her name: ',
}

/** The image and clip models the agent can drive (one per spec), with the configured defaults. */
export async function listModels(client: AgentdClient): Promise<ModelLists> {
  const d = await invoke(client, 'generation_models', {})
  const read = (k: string): ModelList => ({ default: String(d[k]?.default || ''), models: (d[k]?.models || []) as GenModel[] })
  return { image: read('image'), video: read('video'), fix: read('fix'), edit: read('edit'), extend: read('extend') }
}

export async function listGenerations(client: AgentdClient, campaign: string): Promise<{ rows: Generation[]; media: Media }> {
  const d = await invoke(client, 'campaign_generations', { campaign })
  return { rows: (d.generations || []) as Generation[], media: mediaFor(String(d.root || '')) }
}

/** Pick one of a step's results — free and instant, no agent turn. */
export async function pickResult(client: AgentdClient, campaign: string, step: string, path: string): Promise<void> {
  await invoke(client, 'step_pick', { campaign, step, path })
}

/** The user's own image as a result of an image step: uploaded into the workspace, then brought
 *  into the step — free and instant, no agent turn. -> its path in the step. */
export async function importImage(client: AgentdClient, campaign: string, step: string, file: File): Promise<string> {
  if (!/\.(png|jpe?g|webp)$/i.test(file.name)) throw new Error(`${file.name}: use a png, jpg or webp image`)
  const read = await readFile(file)
  const res: any = await client.request('workspace.upload', {
    agentId: AGENT_ID,
    path: 'uploads',
    name: file.name,
    dataBase64: read.dataBase64,
  })
  if (!res?.ok) throw new Error(String(res?.error || 'upload failed'))
  const d = await invoke(client, 'step_import', { campaign, step, path: String(res.path) })
  return String(d.media?.path || '')
}

/** A step of the user's own — free and instant, no agent turn. -> the new step's id. */
export async function addStep(
  client: AgentdClient,
  campaign: string,
  step: { title: string; action: 'images' | 'video' | 'product_sheet'; prompt: string; cast: boolean; shows_product: boolean; source?: string },
): Promise<string> {
  const d = await invoke(client, 'step_add', { campaign, ...step })
  return String(d.step?.id || '')
}

/** The user's click: approve exactly this run (its model, count, length, target) before sending it.
 *  -> a one-time token the message carries; the tool refuses the run without it. */
export async function approveRun(
  client: AgentdClient,
  campaign: string,
  step: string,
  tool: CommandTool,
  args: Record<string, unknown>,
): Promise<string> {
  const d = await invoke(client, 'run_approval', { campaign, step, action: 'approve', tool, args })
  return String(d.token || '')
}

export async function dismissProposal(client: AgentdClient, campaign: string, step: string): Promise<void> {
  await invoke(client, 'run_approval', { campaign, step, action: 'dismiss' })
}

export async function setApproval(client: AgentdClient, campaign: string, approval: 'ask' | 'auto'): Promise<void> {
  await invoke(client, 'campaign_settings', { campaign, approval })
}

/** Change a step in place (skip it, open it again) — free and instant, no agent turn. */
export async function updateStep(
  client: AgentdClient,
  campaign: string,
  step: string,
  changes: Partial<Pick<CampaignStep, 'status' | 'title' | 'text'>>,
): Promise<void> {
  await invoke(client, 'step_update', { campaign, step, ...changes })
}

/* ── what costs: a chat message carrying the exact call ─────────────────────────────────────── */

/** The tools a window message may ask for. */
export type CommandTool = 'step_run' | 'still_fix' | 'clip_edit' | 'cast_create'

/** One piece of a text ad's words, set in real fonts over its picture. */
export interface TextLayer {
  role: string
  text: string
  /** x, y, width, height — fractions of the frame. */
  box: [number, number, number, number]
  font: string
  /** The largest letter size, as a fraction of the frame's height (shrunk to fit its area). */
  size: number
  color: string
  align: 'left' | 'center' | 'right'
  kind: 'text' | 'pill'
  fill: string
}

/** Edit a real-font text ad's words — free and instant, so called directly (no agent turn). */
export async function posterText(client: AgentdClient, params: Record<string, unknown>): Promise<any> {
  return invoke(client, 'poster_text', params)
}

/** The message the agent runs as written: a line a person can read, then the call itself. The
 *  agent copies the JSON into the tool unchanged (AGENTS.md, "Messages from the window"). */
export function command(say: string, tool: CommandTool, args: Record<string, unknown>): string {
  // Empty fields are left out (the step's own settings fill them); an empty reference LIST stays —
  // "no references" is a choice.
  const clean = Object.fromEntries(Object.entries(args).filter(([, v]) => v !== '' && v !== undefined && v !== null && v !== 0))
  return `${say}\n${tool} ${JSON.stringify(clean)}`
}

/* ── selection ────────────────────────────────────────────────────────────────────────────── */

/** Something the user selected — an image or a clip, from a step or the gallery. A typed chat
 *  message carries the selection's exact paths, so the agent acts on exactly these files; a
 *  Generate panel takes selected images as references. */
export interface Selected {
  path: string
  kind: 'image' | 'video'
  campaign: string
  /** The step it belongs to, when it belongs to one. */
  shot: string
  /** A URL the window can draw it from. */
  src: string
}

/** The block a typed chat message carries for the selection. */
export function selectionBlock(items: Selected[]): string {
  if (!items.length) return ''
  const camp = Array.from(new Set(items.map((i) => i.campaign).filter(Boolean)))
  return (
    `\n\nSelected${camp.length === 1 ? ` (campaign ${camp[0]})` : ''}:\n` +
    items.map((i) => `- ${i.kind} ${i.path}${i.shot ? ` (step ${i.shot})` : ''}`).join('\n')
  )
}

export function money(usd: number): string {
  return `$${(usd || 0).toFixed(2)}`
}

export const isVideo = (path: string) => /\.(mp4|mov|webm)$/i.test(path)

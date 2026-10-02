/* Ad Studio's campaigns, as the window sees them — READ ONLY.
 *
 * Three of the agent's own tools answer here, called with `tools.invoke` (no model turn, nothing
 * paid): campaign_list, campaign_status, cast_list. Their `details` carry the data; the text is
 * for people and never parsed.
 *
 * THE WINDOW NEVER ACTS THROUGH A TOOL. Moving a campaign past a gate, picking a still, a redo or
 * a fix is the USER'S ANSWER, sent as a chat message (see `composeAnswer`). The daemon stamps that
 * message as the answer to the gate's ask, which is the only thing campaign_run accepts — so a
 * button here can never pass a gate the user did not answer.
 */

import type { AgentdClient } from '@agentd/client'

import { fileUrl } from './artifacts'

export type Gate = 'brief' | 'sheet' | 'stills' | 'clips' | 'done'

export interface ShotOutcome {
  shot_id: string
  status: string
  stills: Record<string, number>
  still: string
  clip: string
  clip_check: string
  problems: string[]
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

export interface CastMember {
  name: string
  description: string
  sheet: string
}

export interface CampaignDetail {
  campaign_id: string
  recipe_key: string
  recipe_title: string
  gate: Gate
  brief: {
    format_key: string
    concept: string
    hook: string
    caption: string
    look: Record<string, string>
    shots: BriefShot[]
  }
  planned: string[]
  shots: ShotOutcome[]
  sheet: string
  sheet_score: number
  spent_usd: number
  product: { name: string; category: string; photos: string[] }
  plan: { shots: string[]; animate: string[]; resolution: string; variants: number; budget_usd: number; gates: string[] }
  direction: Record<string, string>
  cast: CastMember | null
  session: string
  /** The clip model chosen for this campaign ("provider/model"), "" = the configured default. */
  video: string
  updated: number
}

export interface VideoModel {
  id: string
  provider: string
  model: string
  label: string
  references: boolean
  price: string
}

export interface CampaignRow {
  id: string
  name: string
  gate: Gate
  recipe: string
  cast: string
  spent_usd: number
  updated: number
  mine: boolean
  cover: string
  stills: number
  clips: number
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
  if (!res?.details) throw new Error(`${name} returned no data`)
  return res.details
}

export async function listCampaigns(
  client: AgentdClient,
  session: string,
): Promise<{ rows: CampaignRow[]; media: Media }> {
  const d = await invoke(client, 'campaign_list', { session })
  return { rows: (d.campaigns || []) as CampaignRow[], media: mediaFor(String(d.root || '')) }
}

export async function campaignStatus(
  client: AgentdClient,
  campaign: string,
): Promise<{ campaign: CampaignDetail; media: Media }> {
  const d = await invoke(client, 'campaign_status', { campaign })
  return { campaign: d.campaign as CampaignDetail, media: mediaFor(String(d.root || '')) }
}

export async function listCast(client: AgentdClient): Promise<{ cast: CastMember[]; media: Media }> {
  const d = await invoke(client, 'cast_list', {})
  return { cast: (d.cast || []) as CastMember[], media: mediaFor(String(d.root || '')) }
}

/** The clip models the agent can drive (one per video spec), and the configured default. */
export async function listVideoModels(client: AgentdClient): Promise<{ models: VideoModel[]; fallback: string }> {
  const d = await invoke(client, 'video_models', {})
  return { models: (d.models || []) as VideoModel[], fallback: String(d.default || '') }
}

/* ── the user's answer at a gate ──────────────────────────────────────────────────────────── */

/** What the user decided for one shot (or the brief / the sheet). */
export type Decision =
  | { kind: 'keep' }
  | { kind: 'pick'; still: string }
  | { kind: 'redo'; change: string }
  | { kind: 'fix'; still: string; change: string }

/** The answer as the message the agent receives. Plain words the agent maps to campaign_run's
 *  `picks` / `redo` and to `still_fix` (AGENTS.md, step 5) — and that the user can read in the
 *  thread as what they said. */
export function composeAnswer(
  campaign: string,
  gate: Gate,
  decisions: Record<string, Decision>,
  /** Lines that are not about one shot — the clip model, say. */
  extra: string[] = [],
): string {
  const lines = Object.entries(decisions).map(([about, d]) => {
    switch (d.kind) {
      case 'keep':
        return `- ${about}: keep`
      case 'pick':
        return `- ${about}: pick ${d.still}`
      case 'redo':
        return `- ${about}: redo — ${d.change}`
      case 'fix':
        return `- ${about}: fix ${d.still} — ${d.change}`
    }
  })
  const all = Object.values(decisions)
  const allKept = all.length > 0 && all.every((d) => d.kind === 'keep')
  if (allKept && !extra.length) return `${campaign} · ${gate} gate: keep everything and continue.`
  return `${campaign} · ${gate} gate:\n${[...lines, ...extra].join('\n')}`
}

export const GATE_ORDER: Gate[] = ['brief', 'sheet', 'stills', 'clips', 'done']

export const GATE_LABEL: Record<Gate, string> = {
  brief: 'Brief',
  sheet: 'Shoot sheet',
  stills: 'Stills',
  clips: 'Clips',
  done: 'Done',
}

export function money(usd: number): string {
  return `$${(usd || 0).toFixed(2)}`
}

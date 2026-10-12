/* Stories, through the agent's story tools (tools.invoke): the occasions coming up and the themes
 * that suit them, the account's stories, and the free direct edits — a field, one shot, approval.
 * Drafting and revising use the agent's model, so they go through the chat instead. */

import type { AgentdClient } from '@agentd/client'

import { invoke } from './posts'

export interface StoryShot {
  id: string
  shows: string
  purpose: string
  kind: 'image' | 'video'
  product: string
  text: string
  count: number
  aspect: string
  seconds: number
  /** The user's own image it starts from ("" = made). */
  start_from: string
  suggested_count: number
  suggested_aspect: string
  suggested_seconds: number
}

export interface Story {
  id: string
  title: string
  theme: string
  occasion: string
  products: string[]
  audience: string
  goal: string
  offer: string
  tone: string
  cast: string
  language: string
  hook: string
  format: 'reel' | 'carousel'
  shots: StoryShot[]
  caption_angle: string
  cta: string
  notes: string
  version: number
  status: 'draft' | 'approved'
  collection: string
  session: string
  auto_pick: boolean
  updated: number
}

export interface StoryTheme {
  key: string
  name: string
  summary: string
  kinds: string[]
  occasions: string[]
  goal: string
  hooks: string[]
  format: 'reel' | 'carousel'
  shots: StoryShot[]
  why: string
  why_now?: string
}

export interface Occasion {
  key: string
  name: string
  next: string
  lead_days: number
  regions: string[]
  wear: string
  jewellery: string
  posting_now: boolean
}

export const SHAPES = ['9:16', '4:5', '1:1', '3:4', '16:9']

export async function suggestStories(client: AgentdClient, products: string[] = []): Promise<{ today: string; occasions: Occasion[]; themes: StoryTheme[] }> {
  const d = await invoke(client, 'story_suggest', { products })
  return { today: String(d.today || ''), occasions: (d.occasions || []) as Occasion[], themes: (d.themes || []) as StoryTheme[] }
}

export async function listStories(client: AgentdClient): Promise<Story[]> {
  return ((await invoke(client, 'story_list', {})).stories || []) as Story[]
}

/** A direct, free change: fields, or `shot` {id, …}, `add_shot`, `remove_shot`, `order`. */
export async function updateStory(client: AgentdClient, story: string, changes: Record<string, unknown>): Promise<Story> {
  return (await invoke(client, 'story_update', { story, ...changes })).story as Story
}

export async function approveStory(client: AgentdClient, story: string): Promise<Story> {
  return (await invoke(client, 'story_approve', { story })).story as Story
}

/** What a shot still needs before it can be made. */
export function unmade(s: StoryShot): string[] {
  const missing: string[] = []
  if (!s.count) missing.push('how many')
  if (!s.aspect) missing.push('shape')
  if (s.kind === 'video' && !s.seconds) missing.push('length')
  return missing
}

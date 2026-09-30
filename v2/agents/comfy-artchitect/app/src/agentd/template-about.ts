/* A template's ABOUT — `about.json` in the template folder: what the template does, in full.
 * The contract is the plugin's (plugins/comfy-bridge/template_about.py); this is the window's side.
 *
 * WHY. One line ("what it makes") and a word per input left a person guessing what a template
 * really does — the MiniMax one never said it takes up to nine pictures or what `<Picture 1>`
 * means. The Save dialog drafts the about from the chat (template_about_draft, model-written)
 * for the person to edit; the Library and Templates cards open it with "About this template".
 */

import type { AgentdClient } from '@agentd/client'

export const TEMPLATE_ABOUT = 'about.json'
export const ABOUT_FORMAT = 'comfy-penguin-about'

export interface AboutInput {
  role: string
  what: string
  tips: string
}

export interface TemplateAbout {
  format: typeof ABOUT_FORMAT
  version: number
  makes: string
  how_it_works: string
  inputs: AboutInput[]
  you_can_change: string[]
  example: { prompt: string; result: string } | null
  needs: { runs_on?: string; credits?: string; vram?: string; time?: string }
  limits: string[]
}

/** What the draft is written from: the chat's steps (workspace-relative paths) and its inputs. */
export interface AboutDraftSource {
  name: string
  description: string
  steps: { role: string; api: string; ui?: string; manifests: string[] }[]
  inputs: { role: string; what: string }[]
}

/** A model-written draft of the about, from the chat (template_about_draft). */
export async function draftAbout(client: AgentdClient, source: AboutDraftSource): Promise<TemplateAbout> {
  const res = (await client.request('tools.invoke', { name: 'template_about_draft', params: source })) as {
    details?: { about?: unknown }
    text?: string
  }
  const about = parseAbout(res?.details?.about)
  if (typeof about === 'string') throw new Error(res?.text || about)
  return about
}

/** An about read from a URL (a Library file or a shipped template); null when there is none. */
export async function readAbout(url: string): Promise<TemplateAbout | null> {
  const res = await fetch(url, { cache: 'no-store' })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`could not read ${TEMPLATE_ABOUT} (HTTP ${res.status})`)
  const about = parseAbout(await res.json())
  if (typeof about === 'string') throw new Error(about)
  return about
}

const text = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')
const lines = (v: unknown): string[] => (Array.isArray(v) ? v.map(text).filter(Boolean) : [])

/** A parsed about, or why it is not one. */
export function parseAbout(raw: unknown): TemplateAbout | string {
  const a = raw as Partial<TemplateAbout> | null
  if (!a || typeof a !== 'object') return 'the about is not an object'
  if (a.format !== ABOUT_FORMAT) return `not a ${ABOUT_FORMAT}`
  if (!text(a.makes) || !text(a.how_it_works)) return 'the about needs what it makes and how it works'
  const ex = a.example as { prompt?: unknown; result?: unknown } | null
  const needs = (a.needs || {}) as Record<string, unknown>
  return {
    format: ABOUT_FORMAT,
    version: Number(a.version) || 1,
    makes: text(a.makes),
    how_it_works: text(a.how_it_works),
    inputs: Array.isArray(a.inputs)
      ? a.inputs
          .map((i) => ({ role: text(i?.role).replace(/^@/, ''), what: text(i?.what), tips: text(i?.tips) }))
          .filter((i) => i.role)
      : [],
    you_can_change: lines(a.you_can_change),
    example: ex && text(ex.prompt) ? { prompt: text(ex.prompt), result: text(ex.result) } : null,
    needs: Object.fromEntries(
      (['runs_on', 'credits', 'vram', 'time'] as const).map((k) => [k, text(needs[k])]).filter(([, v]) => v),
    ),
    limits: lines(a.limits),
  }
}

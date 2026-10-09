/* The STAGES of this chat's pipeline — what each will do, what each has made, and what the person
 * picked — and the three things the Stages panel does with them.
 *
 * READ OFF DISK, like the slot record (reference-slots.ts). The plugin keeps three records in the
 * chat's stages folder (`workflows/<chat>/stages/`):
 *
 *   stage_plan.json     what each stage WILL do: model, inputs, size, length, prompt — written by
 *                       the plugin on every design save (plugins/comfy-bridge/stage_plan_view.py)
 *   pipeline_runs.json  what each stage HAS done: status, every result, the inputs it was given
 *                       (pipeline_run_record.py)
 *   stage_picks.json    which result of each stage goes on — THIS window's file (stage_picks.py)
 *
 * WHAT A CLICK DOES, the Ad Studio way:
 *   pick a result      instant and free: this window rewrites stage_picks.json
 *   set an input       instant and free: the chosen file is copied into the chat's references
 *                      folder under the input's role — the same slot the Inputs section shows,
 *                      so both always agree
 *   Run                records a one-time approval (stage_run_approve, a window-only tool), then
 *                      sends the chat the exact call carrying it; pipeline_run refuses without it.
 */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import { fileUrl, type Artifact } from './artifacts'
import { AGENT_ID } from './client'
import { copy, upload, utf8Base64 } from './library'
import { applyApprovedDeletions, chatDirFor, relOfChatFile } from './workspace-files'

export const PLAN_FILE = 'stage_plan.json'
export const RUNS_FILE = 'pipeline_runs.json'
export const PICKS_FILE = 'stage_picks.json'

export interface StageInputFact {
  role: string
  /** "you" — a file the person adds — or the name of the stage that makes it. */
  from: string
  output: string
  /** "first" / "last": the clip opens or ends on this file exactly as given. */
  frame: string
  /** IMAGE / VIDEO / AUDIO, or "" when the recipe does not say. */
  type: string
}

export interface StageFacts {
  model: string
  loras: string[]
  size: string
  frames: number | null
  fps: number | null
  seconds: number | null
  inputs: StageInputFact[]
  review: boolean
  prompt: string
  /** A Seedream stage (made by the provider, not ComfyUI): its frame, how many pictures, and what
   *  they are expected to cost in provider dollars (null when no price is configured). */
  aspect?: string
  images?: number
  cost_usd?: number | null
}

export interface PlanStage {
  name: string
  facts: StageFacts
}

export interface StageRun {
  status: string
  why?: string
  /** Every result, by output name, oldest run first. */
  results: Array<{ at: number; outputs: Record<string, string[]> }>
  /** {role: the file each input was given} on the last run. */
  fed: Record<string, string>
}

export interface StagePlan {
  name: string
  stages: PlanStage[]
  runs: Record<string, StageRun>
  picks: Record<string, string>
}

const stagesDir = (sessionKey: string): string => `${chatDirFor('workflows', sessionKey)}/stages`

/** The named JSON files of one folder, by name; a file that is not there is null. */
async function readJsons(client: AgentdClient, dir: string, names: string[]): Promise<unknown[]> {
  const res = (await client.request('workspace.list', { agentId: AGENT_ID, path: dir })) as {
    entries?: Array<{ name?: unknown; path?: unknown }>
  }
  const entries = res?.entries || []
  return Promise.all(
    names.map(async (name) => {
      const entry = entries.find((e) => e?.name === name)
      if (!entry?.path) return null
      const got = await fetch(fileUrl(String(entry.path)))
      if (!got.ok) throw new Error(`${name}: HTTP ${got.status}`)
      return got.json()
    }),
  )
}

function toPlan(plan: unknown, runs: unknown, picks: unknown): StagePlan | null {
  const p = plan as { name?: unknown; stages?: unknown } | null
  if (!p || !Array.isArray(p.stages) || !p.stages.length) return null
  const r = (runs && typeof runs === 'object' ? runs : {}) as Record<string, Partial<StageRun>>
  return {
    name: String(p.name || ''),
    stages: p.stages as PlanStage[],
    runs: Object.fromEntries(
      Object.entries(r).map(([k, v]) => [
        k,
        { status: String(v?.status || ''), why: v?.why, results: Array.isArray(v?.results) ? v!.results! : [], fed: v?.fed || {} },
      ]),
    ),
    picks: (picks && typeof picks === 'object' ? picks : {}) as Record<string, string>,
  }
}

/** This chat's stages, re-read whenever the workspace changes (a design save, a run, a pick). */
export function useStagePlan(client: AgentdClient | undefined, sessionKey: string, version: number): StagePlan | null {
  const [plan, setPlan] = useState<StagePlan | null>(null)
  useEffect(() => {
    if (!client || !sessionKey) {
      setPlan(null)
      return
    }
    let stale = false
    void readJsons(client, stagesDir(sessionKey), [PLAN_FILE, RUNS_FILE, PICKS_FILE])
      .then(([p, r, k]) => {
        if (!stale) setPlan(toPlan(p, r, k))
      })
      .catch((e) => {
        console.error('[stages] could not read the stage records: %s', String((e as Error)?.message || e))
        if (!stale) setPlan(null)
      })
    return () => {
      stale = true
    }
  }, [client, sessionKey, version])
  return plan
}

/** Every file a stage has made, newest first, workspace-relative. */
export function resultsOf(run: StageRun | undefined): string[] {
  const out: string[] = []
  for (const r of [...(run?.results || [])].reverse()) for (const files of Object.values(r.outputs || {})) out.push(...files)
  return out.filter((f, i) => out.indexOf(f) === i)
}

/** The result that goes on: the person's pick when it is one of the stage's results, else the latest. */
export function pickedResult(plan: StagePlan, stage: string): string {
  const all = resultsOf(plan.runs[stage])
  const pick = plan.picks[stage]
  return pick && all.includes(pick) ? pick : all[0] || ''
}

/** Pick a result of a stage — what the stages after it are given. */
export async function savePick(client: AgentdClient, sessionKey: string, plan: StagePlan, stage: string, rel: string): Promise<void> {
  const picks = { ...plan.picks, [stage]: rel }
  await upload(client, stagesDir(sessionKey), PICKS_FILE, utf8Base64(JSON.stringify(picks, null, 1)), true)
}

/** Put any file of this chat into an input the person fills: copied under the role's name into the
 *  chat's references folder — one file per role, so the previous one (any extension) goes. */
export async function setInput(
  client: AgentdClient,
  sessionKey: string,
  role: string,
  from: Artifact,
  current: Artifact | null,
): Promise<void> {
  const rel = relOfChatFile(from.path, sessionKey)
  if (!rel) throw new Error(`${from.name} is not a file of this chat`)
  const ext = (from.name.match(/\.[^.]+$/) || [''])[0].toLowerCase()
  const to = `${chatDirFor('references', sessionKey)}/${role}${ext}`
  if (rel === to) return
  const old = current ? relOfChatFile(current.path, sessionKey) : null
  if (old && old !== to) await applyApprovedDeletions(client, [old])
  await copy(client, rel, to, true)
}

/** The Run click: a one-time approval for this stage of this chat. -> its token. */
export async function approveStage(client: AgentdClient, sessionKey: string, stage: string): Promise<string> {
  const res = (await client.request('tools.invoke', {
    name: 'stage_run_approve',
    params: { session: sessionKey, stage },
  })) as { text?: string; details?: { token?: unknown } }
  const token = String(res?.details?.token || '')
  if (!token) throw new Error(res?.text || 'the run was not approved')
  return token
}

/** What the chat is sent after the click: the exact call, carrying the approval. */
export function runMessage(stage: string, token: string): string {
  return `Run the ${stage.replace(/_/g, ' ')} step.\npipeline_run ${JSON.stringify({ stage, approval: token })}`
}

/** "2 pictures · 9:16 · ≈ $0.24" for a Seedream stage; "" otherwise. */
export function picturesText(f: StageFacts): string {
  if (!f.images) return ''
  const cost = typeof f.cost_usd === 'number' ? ` · ≈ $${f.cost_usd.toFixed(2)} in credits` : ''
  return `${f.images} picture${f.images === 1 ? '' : 's'} · ${f.aspect || ''}${cost}`
}

/** "10 s · 241 frames at 24 fps", or the frames alone when the fps is not set by a port. */
export function lengthText(f: StageFacts): string {
  if (f.seconds) return `${f.seconds} s` + (f.frames ? ` · ${f.frames} frames${f.fps ? ` at ${f.fps} fps` : ''}` : '')
  return f.frames ? `${f.frames} frames` : ''
}

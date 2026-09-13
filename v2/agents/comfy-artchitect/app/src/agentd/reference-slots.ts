/* Reference SLOTS — the roles a workflow needs, and which file fills each.
 *
 * THE PROBLEM THIS ENDS. References used to be loose uploads announced by a chat message: the
 * agent guessed which file was which from its name, could not tell whether it had all of them,
 * stopped after each one to report the upload, and a job that needed three images and got two
 * ran anyway or stalled in prose.
 *
 * THE MECHANISM, SHARED WITH THE PLUGIN (plugins/comfy-bridge/reference_slots.py). The agent
 * declares roles — in the ask (`references: [{role, what}]`) and in the workflow itself, where a
 * loader's file input is the token `@model`. A slot is FILLED by a file in this chat's references
 * folder whose stem is the role: `references/<chat>/model.jpg`. This window writes exactly that
 * name when the user drops a file on the slot, so nobody names anything and the agent never needs
 * to see a pixel to know which file is which. `comfy_run` resolves the tokens against the folder
 * and REFUSES while any slot is empty — the gate is a file check on the daemon, not a sentence.
 *
 * WHERE THE SLOTS COME FROM. Two sources, merged: the latest `ask_user` call in the thread (its
 * `references` argument — the earliest a slot is known, so the user can start filling them while
 * the design is still being settled) and the plugin's record `.slots.json` beside the files,
 * written by every emit that carries tokens. The record is read through `/file` like any other
 * workspace file; it sits in the chat's folder, so it is per chat by construction.
 */

import { useEffect, useState } from 'react'

import { fileUrl, type Artifact } from './artifacts'
import type { ThreadItem } from './chat'
import { chatDirFor } from './workspace-files'

export const SLOTS_RECORD = '.slots.json'

export interface Slot {
  role: string
  what: string
  /** The workflows (by role name) that use this slot — empty while only the ask declared it. */
  workflows: string[]
  /** The file filling it, or null while empty. */
  file: Artifact | null
}

interface Declared {
  what: string
  workflows: string[]
}

const norm = (p: string): string => p.replace(/\\/g, '/')
const stem = (name: string): string => name.replace(/\.[^.]*$/, '')

/** Files that live in THIS chat's references folder. */
export function chatReferences(listed: Artifact[], sessionKey: string): Artifact[] {
  const dir = `/${chatDirFor('references', sessionKey)}/`
  return listed.filter((a) => norm(a.path).includes(dir))
}

/** When the latest ask was made (ms), or 0 with no ask. */
export function latestAskTs(items: ThreadItem[]): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'tool' && it.name === 'ask_user' && it.done && !it.isError) return it.ts || 0
  }
  return 0
}

/** Roles the latest ask declared, in its order. A later ask replaces an earlier one: a redesign
 *  that needs different inputs must not leave the old slots on screen. */
export function slotsFromThread(items: ThreadItem[]): Array<{ role: string; what: string }> {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind !== 'tool' || it.name !== 'ask_user' || !it.done || it.isError) continue
    const raw = (it.args as { references?: unknown }).references
    if (!Array.isArray(raw)) return []
    const out: Array<{ role: string; what: string }> = []
    for (const r of raw) {
      if (!r || typeof r !== 'object') continue
      const role = String((r as { role?: unknown }).role || '')
        .trim()
        .replace(/^@/, '')
      if (role) out.push({ role, what: String((r as { what?: unknown }).what || '').trim() })
    }
    return out
  }
  return []
}

/** The plugin's record beside the files, or {} when no emit has declared a slot yet. */
export async function readSlotRecord(refs: Artifact[]): Promise<Record<string, Declared>> {
  const rec = refs.find((a) => a.name === SLOTS_RECORD)
  if (!rec) return {}
  const res = await fetch(fileUrl(rec.path))
  if (!res.ok) throw new Error(`slot record: HTTP ${res.status}`)
  const data = (await res.json()) as unknown
  if (!data || typeof data !== 'object') return {}
  const out: Record<string, Declared> = {}
  for (const [role, v] of Object.entries(data as Record<string, unknown>)) {
    const d = (v || {}) as { what?: unknown; workflows?: unknown }
    out[role] = {
      what: String(d.what || ''),
      workflows: Array.isArray(d.workflows) ? d.workflows.map(String) : [],
    }
  }
  return out
}

/** Record ∪ ask, in ask order then record order, each matched to the file with its stem. */
export function mergeSlots(
  record: Record<string, Declared>,
  fromAsk: Array<{ role: string; what: string }>,
  refs: Artifact[],
): Slot[] {
  const files = new Map<string, Artifact>()
  for (const a of refs) {
    if (a.name === SLOTS_RECORD || a.name.startsWith('.')) continue
    if (!files.has(stem(a.name))) files.set(stem(a.name), a)
  }
  const order: string[] = []
  const what: Record<string, string> = {}
  for (const s of fromAsk) {
    if (!order.includes(s.role)) order.push(s.role)
    if (s.what) what[s.role] = s.what
  }
  for (const [role, d] of Object.entries(record)) {
    if (!order.includes(role)) order.push(role)
    if (d.what && !what[role]) what[role] = d.what
  }
  return order.map((role) => ({
    role,
    what: what[role] || '',
    workflows: record[role]?.workflows || [],
    file: files.get(role) || null,
  }))
}

/** Files in the chat's folder that fill no slot — added before the roles were known, or extra. */
export function freeReferences(refs: Artifact[], slots: Slot[]): Artifact[] {
  const taken = new Set(slots.map((s) => s.file?.path).filter(Boolean))
  return refs.filter((a) => a.name !== SLOTS_RECORD && !a.name.startsWith('.') && !taken.has(a.path))
}

/** The slots as the rail shows them. Re-derived whenever the thread or the listing changes; the
 *  record is fetched again only when the listing changes, which is when the plugin rewrites it. */
export function useReferenceSlots(
  items: ThreadItem[],
  listed: Artifact[],
  sessionKey: string,
): { slots: Slot[]; free: Artifact[] } {
  const refs = chatReferences(listed, sessionKey)
  const [record, setRecord] = useState<Record<string, Declared>>({})
  const recordKey = refs
    .filter((a) => a.name === SLOTS_RECORD)
    .map((a) => `${a.path}:${a.size ?? 0}`)
    .join('|')

  useEffect(() => {
    let stale = false
    if (!recordKey) {
      setRecord({})
      return
    }
    void readSlotRecord(refs)
      .then((r) => {
        if (!stale) setRecord(r)
      })
      .catch(() => {
        if (!stale) setRecord({})
      })
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by what would change the record
  }, [recordKey, sessionKey])

  /* THE EMITTED GRAPHS ARE THE TRUTH ONCE THEY EXIST. An ask declares slots early so the user
     can start filling them; but a design that moved on after the ask (a `dataset` slot the
     agent then abandoned for a one-photo route) must not keep asking for a file nothing will
     read. So: ask-only slots show until an emit NEWER than the latest ask has written the
     record; from then on only the record's roles are slots, and the ask's extras are gone. */
  const recordTs = refs.find((a) => a.name === SLOTS_RECORD)?.modified || 0
  const recordNewer = recordTs * 1000 > latestAskTs(items)
  const slots = mergeSlots(record, recordNewer ? [] : slotsFromThread(items), refs)
  return { slots, free: freeReferences(refs, slots) }
}

/** The one message that hands filled slots to the agent — sent once all declared slots are
 *  filled, never per file. First person, like the composer, and it names the mapping so the
 *  agent has nothing to guess. */
export function referencesReadyInstruction(slots: Slot[]): string {
  const pairs = slots.map((s) => `@${s.role} = ${s.file?.name || '?'}`).join(', ')
  return (
    `All reference slots are filled: ${pairs}. ` +
    `Continue — validate the workflow and run it; comfy_run uploads and wires them in itself.`
  )
}

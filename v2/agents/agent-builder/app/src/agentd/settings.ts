/* The settings surface — the whole agentd config, plus this agent's own overrides.
 *
 * TWO LAYERS, ONE PAGE.
 *
 *   daemon    agentd.config.json + .env, shared by every agent on the machine
 *   agent     config.agents["agent-builder"], this agent alone
 *
 * `config.agents` is an ordinary config key (same nested shape as `plugins`), so both layers
 * arrive in ONE config.get and leave in ONE config.set. There is no second store.
 *
 * The override flag decides which layer wins AT RUN TIME, key by key: for each knob this agent
 * has set, its value; for the rest, the daemon's. Off, and the agent's block is ignored entirely
 * — kept on disk, just dormant.
 *
 * EVERY OVERRIDABLE ROW SAYS WHICH LAYER IT CAME FROM. That is not decoration. This page once
 * showed "Model: GPT-5" while every turn was answered by gemini, because cost-efficiency was
 * silently overriding it — a value with no provenance is how that stayed invisible.
 *
 * Edits are held in `draft`, a copy of the daemon's values that dotted-key paths write into
 * (`cost_efficiency.enabled`, `agents.agent-builder.model`). The patch sent to the daemon is the
 * set of TOP-LEVEL keys that differ, which is exactly what config.set accepts.
 *
 * Provider keys are the reason the page exists at all — BYOK. `env` says which are set;
 * `envValues` carries the actual strings and is ABSENT for an installed agent (the daemon strips
 * it), so the field renders as "•••• saved" with no way to read it back. Intended, not a failure:
 * a page that shipped inside someone else's package must never lift the user's key. Keys are
 * DAEMON-WIDE and deliberately not overridable — one .env, one source.
 */

import type { AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AGENT_ID } from './client'

export interface DeclaredField {
  key: string
  label?: string
  kind?: string
  required?: boolean
  help?: string
}

export interface CatalogOption {
  value?: string
  id?: string
  label?: string
  name?: string
}

export interface ConfigData {
  values: Record<string, any>
  env: Record<string, boolean>
  /** ABSENT for an installed agent. Presence is the "can this page reveal a saved key" test. */
  envValues?: Record<string, string>
  providerKeys: string[]
  settings: DeclaredField[]
  settingsValues: Record<string, string>
  envOverrides: Record<string, string>
  catalogs: Record<string, Array<string | CatalogOption>>
  effectiveModel?: string
  version?: string
  keysLocked?: boolean
}

/** One control on the page. */
export interface FieldSpec {
  key: string
  label: string
  type: 'text' | 'number' | 'toggle' | 'select'
  options?: string[]
  catalog?: string
  help?: string
  /** Does this field belong to the agent layer? */
  agent?: boolean
}

// ── nested values, addressed by dotted path ─────────────────────────────────
// Agent ids are kebab-case slugs, so `agents.<id>.model` splits unambiguously.
export function getPath(obj: any, path: string): any {
  return path.split('.').reduce((acc, k) => (acc == null ? undefined : acc[k]), obj)
}

export function setPath(obj: any, path: string, value: unknown): any {
  const parts = path.split('.')
  const root = { ...obj }
  let cur = root
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i]
    cur[k] = cur[k] && typeof cur[k] === 'object' ? { ...cur[k] } : {}
    cur = cur[k]
  }
  cur[parts[parts.length - 1]] = value
  return root
}

const OVERRIDE_PATH = `agents.${AGENT_ID}.override_default`

export type Tone = '' | 'ok' | 'bad'

export function useSettings(client: AgentdClient) {
  const [data, setData] = useState<ConfigData | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [loadError, setLoadError] = useState('')
  const [message, setMessage] = useState<{ text: string; tone: Tone }>({ text: '', tone: '' })

  const load = useCallback(async () => {
    try {
      const res = (await client.request('config.get')) as ConfigData
      setData(res)
      // a fresh copy every load, so Save -> reload leaves nothing stale behind
      setDraft(JSON.parse(JSON.stringify(res.values || {})))
      setKeys({})
      setLoadError('')
    } catch (e) {
      setLoadError(String((e as Error)?.message || e))
    }
  }, [client])

  useEffect(() => {
    void load()
  }, [load])

  /** Default TRUE — the flag exists to be turned OFF, and must read the same way the resolver
   *  does or the page would describe behaviour the daemon does not have. */
  const overriding = getPath(draft, OVERRIDE_PATH) !== false

  /** Where a field's value lives. An agent-scoped field writes into this agent's own block while
   *  the override is on; with it off the agent's block does nothing, so the row shows the
   *  daemon's value and says so. */
  const pathFor = useCallback(
    (f: { key: string; agent?: boolean }) =>
      f.agent && overriding ? `agents.${AGENT_ID}.${f.key}` : f.key,
    [overriding],
  )

  /** The value to SHOW: this agent's if it set one, else the daemon's. Mirrors resolve(). */
  const valueOf = useCallback(
    (f: { key: string; agent?: boolean }) => {
      if (f.agent && overriding) {
        const own = getPath(draft, `agents.${AGENT_ID}.${f.key}`)
        if (own !== undefined) return own
      }
      return getPath(draft, f.key)
    },
    [draft, overriding],
  )

  /** "this agent" or "daemon" — the badge. */
  const sourceOf = useCallback(
    (f: { key: string; agent?: boolean }): 'this agent' | 'daemon' =>
      f.agent && overriding && getPath(draft, `agents.${AGENT_ID}.${f.key}`) !== undefined
        ? 'this agent'
        : 'daemon',
    [draft, overriding],
  )

  /** Is cost efficiency on for this layer? Decides whether a `model` row means anything. */
  const costEfficiencyOn = useCallback(
    (agentScoped: boolean) => !!(valueOf({ agent: agentScoped, key: 'cost_efficiency' }) || {}).enabled,
    [valueOf],
  )

  const setValue = useCallback(
    (f: { key: string; agent?: boolean }, value: unknown) =>
      setDraft((prev) => setPath(prev, f.agent && overriding ? `agents.${AGENT_ID}.${f.key}` : f.key, value)),
    [overriding],
  )

  const setOverride = useCallback(
    (on: boolean) => setDraft((prev) => setPath(prev, OVERRIDE_PATH, on)),
    [],
  )

  const setKey = useCallback(
    (name: string, value: string) => setKeys((prev) => ({ ...prev, [name]: value })),
    [],
  )

  /** What actually goes to the daemon: the TOP-LEVEL keys whose value differs from what was
   *  loaded. Nested edits ride inside their own top-level key, which is why config.set needs no
   *  notion of paths. */
  const patch = useMemo(() => {
    const out: Record<string, any> = {}
    const values = data?.values || {}
    for (const k of Object.keys(draft)) {
      if (JSON.stringify(draft[k]) !== JSON.stringify(values[k])) out[k] = draft[k]
    }
    return out
  }, [draft, data])

  const dirty = Object.keys(patch).length > 0 || Object.keys(keys).length > 0

  const commit = useCallback(async () => {
    setMessage({ text: 'saving…', tone: '' })
    try {
      const params: Record<string, unknown> = {}
      if (Object.keys(patch).length) params.patch = patch
      if (Object.keys(keys).length) params.keys = keys
      const res: any = await client.request('config.set', params)
      setMessage({
        text: res?.restartRequired
          ? 'Saved — restart the daemon for some of these to take effect.'
          : 'Saved.',
        tone: 'ok',
      })
      await load() // reseeds draft from what the daemon actually stored
    } catch (e) {
      // Leave the edits in place. Clearing the form on failure would throw away what the user
      // typed and tell them nothing.
      setMessage({ text: `could not save: ${String((e as Error)?.message || e)}`, tone: 'bad' })
    }
  }, [client, patch, keys, load])

  return {
    data,
    loadError,
    message,
    setMessage,
    overriding,
    setOverride,
    valueOf,
    sourceOf,
    pathFor,
    setValue,
    setKey,
    costEfficiencyOn,
    dirty,
    commit,
    reload: load,
  }
}

/* useSettings 窶・the two layers of configuration, and the draft between them and Save.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source. If you need something it does not expose, add it there so every agent gets it.
 *
 * TWO LAYERS, ONE RULE: this agent's value wins, else the daemon's 窶・key by key. Key-by-key is
 * what makes it safe: an agent that sets only `model` still inherits reasoning effort, turn limit
 * and the rest, rather than booting with nothing.
 *
 * THERE IS NO OVERRIDE SWITCH, and there was one. "Off" meant "use the daemon's values", which
 * next to cost efficiency 窶・a knob that OVERWRITES the model every turn 窶・produced an agent that
 * named its model, watched the daemon's cheap one answer anyway, and had nothing on screen to
 * explain which layer had won. One layer decides. Nothing to arbitrate.
 *
 * PROVENANCE IS NOT DECORATION. Every row says which layer produced its value, because a page that
 * shows a value without saying where it came from is the page that displayed one model while
 * another answered every turn.
 */

import type { AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CatalogOption, FieldSpec } from './schema'

/** What `config.get` hands back. Only the parts this page renders are named. */
export interface ConfigData {
  values?: Record<string, any>
  /** Provider key NAMES; the values are never sent back (see `envValues`). */
  providerKeys?: string[]
  /** Which of them are set, by name. */
  env?: Record<string, boolean>
  /** Present ONLY where this page may reveal a key 窶・absent for an installed agent. */
  envValues?: Record<string, string>
  /** Keys pinned by the environment, which the config file cannot win against. */
  envOverrides?: Record<string, string>
  /** What this agent's AUTHOR shipped in `agent.config.json`, key by key. */
  authored?: Record<string, unknown>
  /** May the person running it change those? FALSE by default 窶・the author's choices are the
   *  author's unless they opted out with `user_editable`. */
  authoredLocked?: boolean
  /** The agent's own declared [[settings]] fields. */
  settings?: DeclaredField[]
  settingsValues?: Record<string, string>
  /** Daemon-owned option lists (models, 窶ｦ) so a new one needs no client release. */
  catalogs?: Record<string, Array<string | CatalogOption>>
  version?: string
  /** The platform manages provider keys on this install 窶・the fields render read-only. */
  keysLocked?: boolean
}

/** One field the agent's own `[[settings]]` declared 窶・whoever RUNS the agent fills these in. */
export interface DeclaredField {
  key: string
  label?: string
  kind?: string
  required?: boolean
  help?: string
}

export type Tone = '' | 'ok' | 'bad'

// 笏笏 nested values, addressed by dotted path 笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏笏
// Agent ids are kebab-case slugs, so `agents.<id>.model` splits unambiguously.
export function getPath(obj: any, path: string): any {
  return path.split('.').reduce((acc, k) => (acc == null ? undefined : acc[k]), obj)
}

/** Remove a key, copying the objects on the way down like setPath. Used to hand a setting back to
 *  the daemon: the agent's own value is DELETED rather than set to anything, because "unset" is
 *  what makes the resolver fall through to the layer below. Writing an empty string or a null
 *  would be an override that happens to be blank. */
export function deletePath(obj: any, path: string): any {
  const parts = path.split('.')
  const root = { ...obj }
  let cur = root
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i]
    if (cur[k] == null || typeof cur[k] !== 'object') return root // nothing there to clear
    cur[k] = { ...cur[k] }
    cur = cur[k]
  }
  delete cur[parts[parts.length - 1]]
  return root
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

/**
 * @param client the connected SDK client.
 * @param agentId WHICH agent's layer to edit. Passed in rather than discovered, because this
 *   module is copied into every agent and each one knows its own id 窶・a module that guessed would
 *   be a module that edited the wrong agent's settings on the day the guess was wrong.
 */
export function useSettings(client: AgentdClient, agentId: string) {
  const [data, setData] = useState<ConfigData | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [loadError, setLoadError] = useState('')
  const [message, setMessage] = useState<{ text: string; tone: Tone }>({ text: '', tone: '' })

  const load = useCallback(async () => {
    try {
      const res = (await client.request('config.get')) as ConfigData
      setData(res)
      // A fresh copy every load, so Save -> reload leaves nothing stale behind.
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

  /** Where a field's value lives. An agent-scoped field always writes into this agent's own
   *  block 窶・this agent's settings decide how this agent runs. */
  const pathFor = useCallback(
    (f: FieldSpec) => (f.agent ? `agents.${agentId}.${f.key}` : f.key),
    [agentId],
  )

  /* THE AUTHOR'S LAYER. `authoredLocked` defaults to TRUE when absent: a daemon that predates
     this feature sends neither field, and guessing "editable" would draw a control the daemon
     then refuses to write. */
  const authored = (data?.authored || {}) as Record<string, unknown>
  const authorLocked = data?.authoredLocked !== false

  /** Did this agent's author decide this knob AND keep it? Then it is not editable here, and the
   *  daemon will drop a write to it 窶・see `config.set`. */
  const lockedByAuthor = useCallback(
    (f: FieldSpec) => !!f.agent && authorLocked && f.key in authored,
    [authorLocked, authored],
  )

  /** The effective value, resolving the SAME three layers the daemon resolves, in the same order.
   *
   *  IT HAS TO MATCH `agent_config.resolve` EXACTLY. A page that resolves differently from the
   *  runtime is the failure this whole layer-labelling exercise exists to prevent: it shows the
   *  daemon's model while the agent answers on the author's, and every explanation of the
   *  difference is wrong. */
  const valueOf = useCallback(
    (f: FieldSpec) => {
      if (f.agent) {
        // Locked: the author's value wins outright, whatever is sitting in this machine's block.
        if (authorLocked && f.key in authored) return authored[f.key]
        const own = getPath(draft, `agents.${agentId}.${f.key}`)
        if (own !== undefined) return own
        if (f.key in authored) return authored[f.key]
      }
      return getPath(draft, f.key)
    },
    [draft, agentId, authored, authorLocked],
  )

  /** Which of the three layers produced the value 窶・the badge beside the row. */
  const sourceOf = useCallback(
    (f: FieldSpec): 'this agent' | 'author' | 'daemon' => {
      if (!f.agent) return 'daemon'
      if (authorLocked && f.key in authored) return 'author'
      if (getPath(draft, `agents.${agentId}.${f.key}`) !== undefined) return 'this agent'
      return f.key in authored ? 'author' : 'daemon'
    },
    [draft, agentId, authored, authorLocked],
  )

  /** Is cost efficiency on for this layer? Decides whether a `model` row means anything. */
  const costEfficiencyOn = useCallback(
    (agentScoped: boolean) =>
      !!(valueOf({ key: 'cost_efficiency', label: '', type: 'toggle', agent: agentScoped }) || {})
        .enabled,
    [valueOf],
  )

  const setValue = useCallback(
    (f: FieldSpec, value: unknown) => setDraft((prev) => setPath(prev, pathFor(f), value)),
    [pathFor],
  )

  /** Hand a setting back to the daemon: drop this agent's own value so the row inherits again.
   *  Only meaningful for an `agent: true` field 窶・nothing else has a layer to fall through to. */
  const clearOverride = useCallback(
    (f: FieldSpec) => {
      if (!f.agent) return
      setDraft((prev) => deletePath(prev, `agents.${agentId}.${f.key}`))
    },
    [agentId],
  )

  const setKey = useCallback(
    (name: string, value: string) => setKeys((prev) => ({ ...prev, [name]: value })),
    [],
  )

  /** What actually goes to the daemon: the TOP-LEVEL keys whose value differs from what was
   *  loaded. Nested edits ride inside their own top-level key, which is why `config.set` needs no
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

  /** Save. Returns whether the daemon's RUNNING copy is now stale 窶・the caller restarts.
   *
   *  It used to say "restart the daemon for some of these to take effect" and stop there, which
   *  is a save reporting success while the process serves the old value. A message is not a
   *  mechanism. Reported rather than acted on here because this hook knows about settings, not
   *  about process lifecycle. */
  const commit = useCallback(async (): Promise<boolean> => {
    setMessage({ text: 'saving窶ｦ', tone: '' })
    try {
      const params: Record<string, unknown> = {}
      if (Object.keys(patch).length) params.patch = patch
      if (Object.keys(keys).length) params.keys = keys
      const res: any = await client.request('config.set', params)
      if (res?.saved === false) {
        // The daemon REFUSED (a locked key, a machine-only knob, a hosted rule). Saying
        // "Saved." here is how a reverted dropdown became unreportable — the page must relay
        // the daemon's own reason, not its own optimism.
        throw new Error(String(res?.error || 'the daemon did not store the change'))
      }
      if (Array.isArray(res?.ignored) && res.ignored.length) {
        setMessage({
          text: `Saved — except ${res.ignored.join(', ')}: ${String(res.ignoredReason || 'locked by the agent author')}`,
          tone: 'bad',
        })
        await load()
        return !!res?.restartRequired
      }
      const restartRequired = !!res?.restartRequired
      setMessage({ text: restartRequired ? 'Saved 窶・restarting窶ｦ' : 'Saved.', tone: 'ok' })
      await load() // reseeds the draft from what the daemon actually stored
      return restartRequired
    } catch (e) {
      // Leave the edits in place. Clearing the form on failure would throw away what the user
      // typed and tell them nothing.
      setMessage({ text: `could not save: ${String((e as Error)?.message || e)}`, tone: 'bad' })
      // A save that failed changed nothing, so there is nothing to restart FOR.
      return false
    }
  }, [client, patch, keys, load])

  return {
    data,
    loadError,
    message,
    setMessage,
    valueOf,
    sourceOf,
    lockedByAuthor,
    pathFor,
    setValue,
    clearOverride,
    setKey,
    costEfficiencyOn,
    dirty,
    commit,
    reload: load,
  }
}

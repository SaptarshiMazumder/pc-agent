/* The person's own keys: the Comfy API key (runs every workflow on Comfy Cloud) and the Civitai key.
 *
 * It is this agent's declared [[settings]] (agent.toml), so it saves exactly the way the
 * Settings page saves it: `config.set` with `keys`, stored per account, never read back — a
 * secret only ever says whether it is set.
 */

import { useCallback, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

export interface OwnKey {
  key: string
  label: string
  help: string
  isSet: boolean
}

/** The keys this agent declares (agent.toml [[settings]]). */
export const AGENT_KEYS = ['USER_COMFY_API_KEY', 'USER_CIVITAI_TOKEN']

export function useOwnComfyKeys(client: AgentdClient | undefined, names: string[] = AGENT_KEYS) {
  const [keys, setKeys] = useState<OwnKey[]>([])
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    if (!client) return
    const data = (await client.request('config.get')) as {
      settings?: { key: string; label?: string; help?: string }[]
      env?: Record<string, boolean>
    }
    const declared = (data.settings || []).filter((f) => names.includes(f.key))
    setKeys(
      declared.map((f) => ({
        key: f.key,
        label: f.label || f.key,
        help: f.help || '',
        isSet: !!(data.env || {})[f.key],
      })),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, names.join()])

  useEffect(() => {
    load().catch((e) => setMessage(`could not read your keys: ${String((e as Error)?.message || e)}`))
  }, [load])

  const save = useCallback(
    async (values: Record<string, string>) => {
      if (!client) return
      setMessage('saving…')
      try {
        const res = (await client.request('config.set', { keys: values })) as { saved?: boolean; error?: string }
        if (res?.saved === false) throw new Error(String(res?.error || 'not stored'))
        setMessage('Saved.')
        await load()
      } catch (e) {
        setMessage(`could not save: ${String((e as Error)?.message || e)}`)
      }
    },
    [client, load],
  )

  return { keys, message, save }
}

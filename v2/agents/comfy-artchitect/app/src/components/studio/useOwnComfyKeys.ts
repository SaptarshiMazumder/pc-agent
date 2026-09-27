/* The person's own keys for THEIR ComfyUI — Comfy (paid models), Hugging Face, Civitai.
 *
 * They are this agent's declared [[settings]] (agent.toml), so they save exactly the way the
 * Settings page saves them: `config.set` with `keys`, stored per account, never read back — a
 * secret only ever says whether it is set. The platform's own keys are never among them and
 * never go to a machine it does not run (comfy_bridge).
 */

import { useCallback, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

export interface OwnKey {
  key: string
  label: string
  help: string
  isSet: boolean
}

/** The keys used on the person's own machine. */
export const OWN_KEYS = ['USER_COMFY_API_KEY', 'USER_HF_TOKEN', 'USER_CIVITAI_TOKEN']
/** The key that finds their Vast machines. */
export const VAST_KEY = ['USER_VAST_API_KEY']

export function useOwnComfyKeys(client: AgentdClient | undefined, names: string[] = OWN_KEYS) {
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

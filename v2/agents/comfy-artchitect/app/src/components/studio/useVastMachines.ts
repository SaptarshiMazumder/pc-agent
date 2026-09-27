/* The machines on the person's own Vast account, read with their Vast API key — the list the
 * Connection section picks from, so connecting Vast is "paste your key, pick your machine".
 */

import { useCallback, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

export interface VastMachine {
  id: number
  name: string
  gpu: string
  status: string
  running: boolean
}

export function useVastMachines(client: AgentdClient | undefined, enabled: boolean) {
  const [machines, setMachines] = useState<VastMachine[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    if (!client) return
    setLoading(true)
    setError('')
    try {
      const res = (await client.request('tools.invoke', {
        name: 'comfy_connect',
        params: { action: 'vast_machines' },
      })) as { details?: { machines?: VastMachine[] } }
      setMachines(res?.details?.machines || [])
    } catch (e) {
      setMachines(null)
      setError(String((e as Error)?.message || e).replace(/^comfy_connect:\s*/, ''))
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    if (enabled) void refresh()
  }, [enabled, refresh])

  return { machines, loading, error, refresh }
}

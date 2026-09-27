/* Where THIS ACCOUNT's ComfyUI runs — a GPU rented from us, the person's own Vast machine, or
 * any ComfyUI address — and the means to choose it.
 *
 * ONE CHOICE FOR EVERY CHAT, AND NOTHING RENTED WITHOUT IT. Until the person picks, `kind` is null
 * and `loaded` true: the window asks (ConnectionPrompt), the GPU warm-up rents nothing, and the
 * agent is told the same by gpu_ensure. Picking "Rent a GPU" is the approval, kept for the account.
 *
 * ONE SOURCE, OWNED BY App: the warm-up, the prompt, the Connection section and the engine chip
 * all read it. The truth lives in the plugin (`comfy_connect`, one record per account).
 */

import { useCallback, useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

export type ConnectionKind = 'rented' | 'user_vast' | 'user_url'

export interface Storage {
  kind: 'volume' | 'workspace_volume' | 'disk' | 'pending' | 'unchecked'
  path: string
  error: string
}

export interface ComfyConnection {
  /** false until the plugin has answered once. */
  loaded: boolean
  /** null = the person has not chosen yet (only meaningful once `loaded`). */
  kind: ConnectionKind | null
  /** The address without its token, for showing. "" for the rented GPU. */
  label: string
  /** The link exactly as the person gave it — what the field shows, so a saved link stays in view. */
  link: string
  /** Models can be downloaded onto it (the rented GPU and a Vast machine can; a bare address cannot). */
  downloads: boolean
  /** Where a Vast machine keeps models — its volume (kept) or its disk (lost with it). */
  storage: Storage | null
  busy: boolean
  /** Why the last choice or read failed — the plugin's own words. */
  error: string
  connect: (url: string) => Promise<boolean>
  /** Connect one of the machines on the person's Vast account. */
  useVastMachine: (id: number) => Promise<boolean>
  /** The Vast machine in use, when one was picked from the account. */
  vastMachineId: number | null
  /** Approve renting a GPU from the platform, for this account. */
  rent: () => Promise<void>
}

interface View {
  kind?: ConnectionKind | null
  label?: string
  link?: string
  vast_machine_id?: number | null
  downloads?: boolean
  storage?: Storage | null
}

/** `connected`: the socket is open. The first ask has to wait for it — asked on a socket still
 *  connecting, it failed with "not connected", nothing asked again, and the section stayed
 *  locked on a choice it never read. Asked again on every reconnect, too. */
export function useComfyConnection(client: AgentdClient | undefined, connected: boolean): ComfyConnection {
  const [view, setView] = useState<View | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const call = useCallback(
    async (params: Record<string, unknown>): Promise<View> => {
      const res = (await client!.request('tools.invoke', { name: 'comfy_connect', params })) as {
        details?: View
      }
      return res?.details || {}
    },
    [client],
  )

  useEffect(() => {
    if (!client || !connected) return
    call({ action: 'status' })
      .then((v) => {
        setView(v)
        setError('')
      })
      // An unreadable record is not a choice: say what broke, and rent nothing.
      .catch((e) => setError(String((e as Error)?.message || e)))
  }, [client, connected, call])

  // A STORAGE CHECK STILL RUNNING (ComfyUI restarting onto the volume): ask again until the
  // machine has answered — `status` reads the answer and keeps it.
  const pending = view?.storage?.kind === 'pending'
  useEffect(() => {
    if (!pending || !client) return
    const t = setInterval(() => {
      call({ action: 'status' }).then(setView).catch((e) => setError(String((e as Error)?.message || e)))
    }, 15_000)
    return () => clearInterval(t)
  }, [pending, client, call])

  const choose = useCallback(
    async (params: Record<string, unknown>): Promise<boolean> => {
      setBusy(true)
      setError('')
      try {
        setView(await call(params))
        return true
      } catch (e) {
        setError(String((e as Error)?.message || e).replace(/^comfy_connect:\s*/, ''))
        return false
      } finally {
        setBusy(false)
      }
    },
    [call],
  )

  const connect = useCallback((url: string) => choose({ action: 'connect', url }), [choose])
  const useVastMachine = useCallback(
    (id: number) => choose({ action: 'use_vast_machine', machine_id: id }),
    [choose],
  )
  const rent = useCallback(async () => {
    await choose({ action: 'rent' })
  }, [choose])

  return {
    loaded: view !== null,
    kind: view?.kind || null,
    label: view?.label || '',
    link: view?.link || '',
    downloads: !!view?.downloads,
    storage: view?.storage || null,
    busy,
    error,
    connect,
    useVastMachine,
    vastMachineId: view?.vast_machine_id ?? null,
    rent,
  }
}

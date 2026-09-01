/* Connected services: the MCP servers this agent DECLARED, and the third-party sign-ins it needs.
 *
 * This is the answer to "why does this agent have no tools", a question that has no answer
 * anywhere else — the model just says it cannot do the thing. So a server that is not up says
 * WHY: a credential nobody filled in, or a server that started and offered nothing.
 */

import type { AgentdClient } from '@agentd/client'
import { useCallback, useEffect, useState } from 'react'

export interface McpServer {
  name: string
  transport?: string
  command?: string[]
  url?: string
  tools?: string[]
  /** Why it is not up. Empty means connected. */
  problem?: string
}

export interface OauthConnection {
  name: string
  connected?: boolean
  account?: string
  scopes?: string[]
}

export function useServices(client: AgentdClient) {
  const [servers, setServers] = useState<McpServer[]>([])
  const [connections, setConnections] = useState<OauthConnection[]>([])
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    try {
      const res: any = await client.request('mcp.status', {})
      setServers((res?.servers as McpServer[]) || [])
      setError('')
    } catch (e) {
      // Not fatal to the page — an older daemon has no mcp.status — but not silent either: the
      // section is absent when there is nothing to say, and a real error shows in place.
      setServers([])
      setError(String((e as Error)?.message || e))
    }
    try {
      const res: any = await client.request('oauth.status', {})
      setConnections((res?.connections as OauthConnection[]) || [])
    } catch {
      setConnections([])
    }
  }, [client])

  useEffect(() => {
    void reload()
  }, [reload])

  /** THIS PAGE OPENS THE WINDOW, not the daemon. On a desktop they are the same machine so it
   *  makes no difference; the moment this page is a tab somewhere else, a daemon calling its own
   *  browser would open a login nobody is sitting in front of. */
  const connect = useCallback(
    async (name: string) => {
      const res: any = await client.request('oauth.connect', { name })
      // The daemon catches the redirect on its own /oauth/callback; this window only has to send
      // the user there. Reloading when the window regains focus is how the row updates when they
      // come back.
      window.open(res.authorizeUrl, '_blank', 'noopener')
      await new Promise<void>((resolve) => {
        window.addEventListener('focus', function once() {
          window.removeEventListener('focus', once)
          resolve()
        })
      })
      await reload()
    },
    [client, reload],
  )

  const disconnect = useCallback(
    async (name: string) => {
      await client.request('oauth.disconnect', { name })
      await reload()
    },
    [client, reload],
  )

  return { servers, connections, error, reload, connect, disconnect }
}

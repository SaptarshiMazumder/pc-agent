/**
 * One-shot RPC from the MAIN process to the daemon — used only by the agent-app shell
 * boot (first-run bundle install + app discovery), where no renderer exists yet to do
 * the talking. Speaks the same 3-frame protocol as every other client; broadcast
 * events riding the socket are ignored. One request per connection: open, ask, close.
 */

import WebSocket from 'ws'

export function gatewayRequest(
  wsUrl: string,
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs = 120_000
): Promise<Record<string, any>> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl)
    const timer = setTimeout(() => {
      try {
        ws.close()
      } catch {
        /* already closed */
      }
      reject(new Error(`${method}: timed out`))
    }, timeoutMs)
    ws.on('open', () => ws.send(JSON.stringify({ type: 'req', id: '1', method, params })))
    ws.on('error', (e) => {
      clearTimeout(timer)
      reject(e instanceof Error ? e : new Error(String(e)))
    })
    ws.on('message', (raw) => {
      let frame: { type?: string; id?: string; ok?: boolean; payload?: Record<string, any> }
      try {
        frame = JSON.parse(String(raw))
      } catch {
        return
      }
      if (frame.type !== 'res' || frame.id !== '1') return // events ride the same socket
      clearTimeout(timer)
      ws.close()
      if (frame.ok) resolve(frame.payload || {})
      else reject(new Error(String(frame.payload?.error || `${method} failed`)))
    })
  })
}

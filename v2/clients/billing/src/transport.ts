/** Electron uses a narrow native bridge; browser hosts use ordinary fetch. */
export async function billingFetch(url: string, init: RequestInit = {}): Promise<Response> {
  type Bridge = { billingRequest?: (url: string, init: {
    method?: string; headers: Record<string, string>; body?: string
  }) => Promise<{ status: number; body: string }> }
  const host = globalThis as { agentd?: Bridge; agentdHost?: Bridge }
  const bridge = typeof host.agentd?.billingRequest === 'function' ? host.agentd : host.agentdHost
  if (typeof bridge?.billingRequest !== 'function') return fetch(url, init)
  const headers: Record<string, string> = {}
  new Headers(init.headers).forEach((value, key) => { headers[key] = value })
  const reply = await bridge.billingRequest(url, {
    method: init.method, headers, body: typeof init.body === 'string' ? init.body : undefined
  })
  return new Response(reply.body, { status: reply.status, headers: { 'Content-Type': 'application/json' } })
}

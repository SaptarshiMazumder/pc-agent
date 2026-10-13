export interface BillingRequest {
  method?: string
  headers?: Record<string, string>
  body?: string
}

/** Only the flavor's public billing routes; never a general URL/header proxy. */
export async function requestBilling(base: string, rawUrl: string, init: BillingRequest = {}) {
  const url = new URL(rawUrl)
  const method = (init.method || 'GET').toUpperCase()
  const allowed = method === 'GET' && ['/products', '/me/credits'].includes(url.pathname)
    || method === 'POST' && url.pathname === '/me/checkout'
  if (!base || url.origin !== new URL(base).origin || !allowed || url.username || url.password) {
    throw new Error('Not a configured billing endpoint')
  }
  if (init.body && init.body.length > 32_768) throw new Error('Billing request is too large')
  const headers: Record<string, string> = {}
  for (const [key, value] of Object.entries(init.headers || {})) {
    if (/^(authorization|content-type|accept)$/i.test(key)) headers[key] = String(value)
  }
  const response = await fetch(url, { method, headers, body: init.body,
    redirect: 'error', signal: AbortSignal.timeout(15_000) })
  return { status: response.status, body: await response.text() }
}

import type { BillingRequest } from './billingTransport'

/** Scoped to the configured accounts service; the server still authorizes every admin action. */
export async function requestAdmin(base: string, rawUrl: string, init: BillingRequest = {}) {
  const url = new URL(rawUrl)
  const method = (init.method || 'GET').toUpperCase()
  if (!base || !/^https?:$/.test(url.protocol) || url.origin !== new URL(base).origin
    || !url.pathname.startsWith('/admin/') || !['GET', 'POST'].includes(method)
    || url.username || url.password) throw new Error('Not a configured admin endpoint')
  if (init.body && init.body.length > 32_768) throw new Error('Admin request is too large')
  const headers: Record<string, string> = {}
  for (const [key, value] of Object.entries(init.headers || {})) {
    if (/^(authorization|content-type|accept)$/i.test(key)) headers[key] = String(value)
  }
  const response = await fetch(url, { method, headers, body: init.body,
    redirect: 'error', signal: AbortSignal.timeout(15_000) })
  return { status: response.status, body: await response.text() }
}

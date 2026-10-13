/** Read the configured flavor's public discovery document from the native host.
 * Renderer fetches are blocked by production CORS on both dev and file:// origins.
 * The IPC caller supplies no URL, so this does not expose a general network proxy.
 */
export async function fetchPlatformDiscovery(base: string): Promise<Record<string, unknown> | null> {
  if (!base) return null
  try {
    const url = new URL(`${base.replace(/\/$/, '')}/.well-known/agentd-platform`)
    if (!['https:', 'http:'].includes(url.protocol)) return null
    const response = await fetch(url, {
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000)
    })
    if (!response.ok) return null
    const body: unknown = await response.json()
    return body && typeof body === 'object' && !Array.isArray(body)
      ? body as Record<string, unknown> : null
  } catch {
    return null
  }
}

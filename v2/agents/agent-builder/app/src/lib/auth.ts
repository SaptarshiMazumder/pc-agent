/* The slice of agentd's `lib/auth` that the byte-identical OrgView copy imports.
 *
 * NOT a port of agentd's auth — this window's real auth lives in `agentd/platform.ts` and the
 * shared `common/auth`. These four names exist so the copied component's import line resolves
 * unchanged; each is the SDK's own implementation wearing agentd's signature.
 */

import { authStatus, billing } from '@agentd/client'
import { useEffect, useState } from 'react'

export { onCreditsChanged } from '@agentd/client'
export type { Catalog, CreditPack, Credits, Purchase } from '@agentd/client'

export interface Session {
  token: string
  accountId: string
  email: string
}

/** Who is signed in, as agentd's OrgView reads it (it wants `accountId` to stop somebody
 *  removing their own seat). The token is deliberately empty: this window never sees one. */
export function useAuthSession(): Session | null {
  const [session, setSession] = useState<Session | null>(null)
  useEffect(() => {
    void authStatus({})
      .then((s) =>
        setSession(s.signedIn ? { token: '', accountId: s.accountId, email: s.email } : null),
      )
      .catch(() => setSession(null))
  }, [])
  return session
}

export async function fetchCatalog(kind = 'credit_pack') {
  return billing({}).catalog(kind)
}

/** Buy — agentd's signature, orgId included (the org shop passes it). */
export async function purchase(productId: string, orgId = '') {
  const page = location.href.split('#')[0]
  return billing({}).buy(productId, /^https?:\/\//.test(page) ? page : '', orgId)
}

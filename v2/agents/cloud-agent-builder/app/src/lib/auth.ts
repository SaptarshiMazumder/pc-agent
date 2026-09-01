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

/** Buy — agentd's signature, orgId included (the org shop passes it). No return URL: the
 *  SDK sends the rail to the accounts service's neutral "checkout finished" page, and the
 *  outcome reaches this window over the credits bus (awaitGrant), never through the tab. */
export async function purchase(productId: string, orgId = '') {
  return billing({}).buy(productId, '', orgId)
}

/** Wait for a checkout begun with `purchase` to grant, then ring the credits bus. */
export function awaitGrant() {
  return billing({}).awaitGrant({})
}

/* WHO THE CALLER IS, for the two questions the UI keeps asking about other people's agents:
 *
 *   enterprise  does this person belong to an organization at all? It decides the default SHIP
 *               destination (their org, not the public marketplace) and which boundary the
 *               "external" tag is drawn against.
 *   emails      author account id -> email, best effort. Org detail names members for an admin;
 *               a plain member gets none and a byline falls back to the short id.
 *
 * ONE COPY, and the same file the desktop builder carries — the two windows must not disagree
 * about whether someone is in an organization, because here that answer decides whether an
 * agent goes to a company or to the public.
 */

import { useEffect, useState } from 'react'

import { useAuthSession } from './auth'
import { fetchMyOrgs, fetchOrgDetail, type OrgMembership } from './orgs'

export type Authorship = {
  /** true once we KNOW the answer — until then the UI must not pick a destination. */
  resolved: boolean
  enterprise: boolean
  myId: string
  orgs: OrgMembership[]
  emails: Record<string, string>
}

const EMPTY: Authorship = { resolved: false, enterprise: false, myId: '', orgs: [], emails: {} }

export function useAuthorship(): Authorship {
  const session = useAuthSession()
  const [state, setState] = useState<Authorship>(EMPTY)

  useEffect(() => {
    if (!session) {
      // Signed out is a KNOWN answer, not a pending one: no account, no orgs, publish is public.
      setState({ ...EMPTY, resolved: true })
      return
    }
    let live = true
    fetchMyOrgs()
      .then(async (d) => {
        const emails: Record<string, string> = {}
        await Promise.all(
          d.orgs.map((o) =>
            fetchOrgDetail(o.id)
              .then((det) => {
                for (const m of det.members || []) if (m.accountId) emails[m.accountId] = m.email || ''
              })
              .catch(() => {}),
          ),
        )
        if (live)
          setState({
            resolved: true,
            enterprise: d.orgs.length > 0,
            myId: session.accountId || '',
            orgs: d.orgs,
            emails,
          })
      })
      .catch(() => {
        // Accounts unreachable: resolved, but claim NO org. Guessing "enterprise" on a failed
        // fetch would default a publish to an org this person may not be in.
        if (live) setState({ ...EMPTY, resolved: true, myId: session.accountId || '' })
      })
    return () => {
      live = false
    }
  }, [session])

  return state
}

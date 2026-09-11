/** Adapt the SDK's balance notification to the shared canvas views' account prop. */
import { useEffect, useMemo, useState } from 'react'
import type { AccountAdapter } from '../../../clients/canvas/src/account'
import { onCreditsChanged } from './page-sdk'

export function useCreditRefresh(account?: AccountAdapter): AccountAdapter | undefined {
  const [revision, setRevision] = useState(0)
  useEffect(() => onCreditsChanged(() => setRevision((value) => value + 1)), [])
  // The shared footer and account settings re-read when their account prop changes. A new
  // prop updates the balance without remounting either view or discarding the chat draft.
  return useMemo(() => account ? { ...account } : undefined, [account, revision])
}

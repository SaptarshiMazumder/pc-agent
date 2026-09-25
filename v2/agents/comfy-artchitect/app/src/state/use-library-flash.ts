/* "It went to the Library" — true for a moment after every successful save.
 *
 * A SAVE HAD NO VISIBLE RESULT. The copy lands in a tab the person is not looking at, so they
 * went and checked. Lighting that tab (and the rail's Library row) for a second answers the
 * question where it would be asked, without a toast that covers the work.
 */

import { useEffect, useState } from 'react'

import { useApp } from './store'

const FLASH_MS = 1200

export function useLibraryFlash(): boolean {
  const n = useApp((s) => s.libraryFlash)
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (!n) return
    setOn(true)
    const t = setTimeout(() => setOn(false), FLASH_MS)
    return () => clearTimeout(t)
  }, [n])
  return on
}

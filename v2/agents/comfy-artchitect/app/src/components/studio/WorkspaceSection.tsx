/* One section of the Workspace — Inputs, Outputs, Workflow, All files — with a head that folds it.
 *
 * SECTIONS, NOT SUB-TABS. The Workspace is ONE place (this chat's files), so everything in it is
 * on one scroll: a person can see that the render came out of that workflow fed by those photos
 * without clicking between views. A head that folds lets the long parts (the full file tree) stay
 * out of the way until they are wanted.
 */

import { ChevronDown, ChevronRight } from 'lucide-react'
import { useState, type ReactNode } from 'react'

export function WorkspaceSection({
  title,
  count,
  attention = false,
  defaultOpen = true,
  children,
}: {
  title: string
  /** "4", "1 of 2" — absent when there is nothing to count. */
  count?: string
  /** Something here is waiting on the person (an empty input slot). */
  attention?: boolean
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={`ws-sec${attention ? ' is-attention' : ''}`}>
      <button type="button" className="ws-sec-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="ws-sec-caret" aria-hidden="true">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <span className="ws-sec-title">{title}</span>
        {count && <span className="ws-sec-count">{count}</span>}
        {attention && <span className="ws-sec-flag">waiting on you</span>}
      </button>
      {open && <div className="ws-sec-body">{children}</div>}
    </section>
  )
}

export default WorkspaceSection

/* The two buttons every still and clip carries: open it full screen, and select it. Spans, not
 * buttons — they sit inside tiles that are buttons themselves. */

import { Check, Expand } from 'lucide-react'

import type { Selected } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function MediaTileActions({
  item,
  title,
  selectable = true,
  onSelect,
}: {
  item: Selected
  title: string
  /** Off for a stage already passed and for pending stills. */
  selectable?: boolean
  /** Overrides the plain toggle — the stills board selects one image per shot. */
  onSelect?: () => void
}) {
  const openViewer = useApp((s) => s.openViewer)
  const selected = useApp((s) => s.selection.some((x) => x.path === item.path))
  const toggle = useApp((s) => s.toggleSelected)
  const stop = (e: React.SyntheticEvent) => {
    e.stopPropagation()
    e.preventDefault()
  }
  return (
    <>
      <span
        role="button"
        tabIndex={0}
        className="tile-expand"
        title="Full view"
        onClick={(e) => {
          stop(e)
          openViewer({ src: item.src, kind: item.kind, title })
        }}
      >
        <Expand size={13} />
      </span>
      {selectable && (
        <span
          role="checkbox"
          aria-checked={selected}
          tabIndex={0}
          className={`tile-select${selected ? ' on' : ''}`}
          title={selected ? 'Selected — your next message is about it. Click to unselect.' : 'Select it — your next message is about it'}
          onClick={(e) => {
            stop(e)
            if (onSelect) onSelect()
            else toggle(item)
          }}
        >
          <Check size={12} strokeWidth={3} />
        </span>
      )}
    </>
  )
}

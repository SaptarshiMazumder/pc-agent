/* The full-view corner button for any image or clip whose own click does something else (selects it,
 * opens a stage, picks a slide). Every piece of media in the app opens full screen — this, or
 * ViewableMedia for media with no click of its own. A span: it sits inside buttons. */

import { Expand } from 'lucide-react'

import { isVideo } from '../agentd/campaigns'
import { useApp, type ViewerItem } from '../state/store'

export function ExpandMedia({
  src,
  path,
  title = '',
  kind,
  set,
}: {
  src: string
  /** Decides image or clip when `kind` is not given. */
  path: string
  title?: string
  kind?: ViewerItem['kind']
  /** The media it sits among, to step through in the full view. */
  set?: ViewerItem[]
}) {
  const openViewer = useApp((s) => s.openViewer)
  return (
    <span
      role="button"
      tabIndex={0}
      className="media-expand"
      title="Full view"
      onClick={(e) => {
        e.stopPropagation()
        e.preventDefault()
        openViewer({ src, kind: kind || (isVideo(path) ? 'video' : 'image'), title: title || path.split('/').pop() || '' }, set)
      }}
    >
      <Expand size={12} />
    </span>
  )
}

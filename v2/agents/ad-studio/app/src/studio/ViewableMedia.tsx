/* An image or clip that opens full screen when clicked — never a download, never a browser tab.
 * Given the set it belongs to (a post's slides, a collection), the viewer steps through all of them.
 * Clips play while hovered. */

import { useApp, type ViewerItem } from '../state/store'

/** A workspace file as the viewer shows it: its URL, whether it is a clip, and its name. */
export function viewerItem(src: string, path: string, title = ''): ViewerItem {
  return { src, kind: /\.(mp4|webm|mov)$/i.test(path) ? 'video' : 'image', title: title || path.split('/').pop() || path }
}

export function ViewableMedia({ item, set, className }: { item: ViewerItem; set?: ViewerItem[]; className?: string }) {
  const openViewer = useApp((s) => s.openViewer)
  const open = (e: React.SyntheticEvent) => {
    e.stopPropagation()
    e.preventDefault()
    openViewer(item, set)
  }
  return item.kind === 'video' ? (
    <video
      className={`viewable${className ? ` ${className}` : ''}`}
      src={item.src}
      title={`${item.title} — full view`}
      muted
      loop
      playsInline
      preload="metadata"
      onClick={open}
      onMouseEnter={(e) => void e.currentTarget.play()}
      onMouseLeave={(e) => e.currentTarget.pause()}
    />
  ) : (
    <img className={`viewable${className ? ` ${className}` : ''}`} src={item.src} alt={item.title} title={`${item.title} — full view`} loading="lazy" onClick={open} />
  )
}

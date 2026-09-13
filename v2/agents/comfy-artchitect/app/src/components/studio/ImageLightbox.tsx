/* The render, at the size it was made — full screen, over everything.
 *
 * WHY THE PANE IS NOT ENOUGH. The centre pane is a COLUMN in a three-column window, and a column
 * has to leave room for the tree and the conversation beside it — so a 1080x1920 vertical render
 * arrives scaled into a few hundred pixels of width. That is fine for "which file is this" and
 * useless for the only question anyone actually asks of a render: is it any good? Judging output
 * is the one job this agent explicitly cannot do for the user (see its AGENTS.md), so the window
 * has to make looking properly possible.
 *
 * AN OVERLAY HERE, UNLIKE THE FILE VIEWER, and the difference is real rather than inconsistent.
 * The viewer is where you WORK — read the graph, copy the JSON, keep the tree in reach — so it
 * earns a column. This is a single glance at one image, and a glance wants every pixel on the
 * screen and then wants to be gone.
 *
 * PORTALLED TO <body>. The studio's columns set `overflow: hidden` and `isolation: isolate`, so
 * an overlay rendered inside them is clipped to a column and stacked under its siblings — the
 * exact trap the account menu hit in the sidebar.
 *
 * IT DOES NOT RE-DOWNLOAD. Same `/file` URL the pane behind it is already showing, so the browser
 * serves this from cache: opening it is free, which is what lets it be a plain click.
 */

import { Download, X } from 'lucide-react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

export function ImageLightbox({
  src,
  name,
  onClose,
}: {
  src: string
  name: string
  onClose: () => void
}) {
  /* ESCAPE CLOSES IT. A full-screen layer with no keyboard way out is a trap for anyone who is
     not using a mouse, and the one key everybody already tries is Escape. Bound on `window` for
     the life of the overlay rather than on the element, which would need focus it may not have. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return createPortal(
    /* THE BACKDROP IS THE CLOSE BUTTON, because that is what everyone tries first. The image
       stops the click from reaching it, so clicking the picture itself does not dismiss the
       thing you opened to look at. */
    <div className="lb" onClick={onClose} role="dialog" aria-modal="true" aria-label={name}>
      <div className="lb-bar" onClick={(e) => e.stopPropagation()}>
        <span className="lb-name st-mono">{name}</span>
        <a className="fv-btn" href={src} download={name} title="Download this file">
          <Download size={15} strokeWidth={1.8} />
        </a>
        <button className="fv-btn" onClick={onClose} title="Close" aria-label="Close">
          <X size={16} strokeWidth={1.8} />
        </button>
      </div>
      <img className="lb-img" src={src} alt={name} onClick={(e) => e.stopPropagation()} />
    </div>,
    document.body,
  )
}

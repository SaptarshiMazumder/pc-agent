/* Dragging a workflow OUT of this window and onto ComfyUI's canvas.
 *
 * THE POINT. ComfyUI loads a workflow by having the `.json` dropped on its graph. Today that means
 * download, find it in the downloads folder, switch tabs, drag it from the file manager. The file
 * is already addressable over HTTP, so the middle three steps are avoidable: drag the row, hover
 * the ComfyUI tab until the browser switches to it, drop on the canvas.
 *
 * `DownloadURL` IS WHAT MAKES IT A FILE. A drag carrying only `text/plain` or `text/uri-list`
 * arrives at a drop target as text, and ComfyUI's handler reads `dataTransfer.files` — so it would
 * see nothing and silently ignore the drop. `DownloadURL` (`mime:name:url`) tells Chromium to
 * MATERIALISE the URL as a file for whoever receives it, which is the difference between a gesture
 * that works and one that looks like it should. It is Chromium-only; the two text flavours are set
 * alongside so a drop into anything else still yields a usable link rather than nothing.
 *
 * WHICH FILE TO DRAG. `comfy_emit` writes a PAIR: `name.json` is the UI format the canvas imports,
 * `name.api.json` is what the server executes. Both are draggable because both are legitimate
 * things to hand somewhere, but the canvas wants the first — see `isCanvasImportable`.
 */

import { fileUrl, type Artifact } from '../../agentd/artifacts'

/** A best-effort MIME for the drag. The artifact's own `mime` is preferred; a workflow written by
 *  the bridge often carries none, and `application/json` is both true and what a receiver expects. */
function mimeFor(file: Artifact): string {
  if (file.mime) return file.mime
  if (/\.json$/i.test(file.name)) return 'application/json'
  if (/\.(md|txt|log)$/i.test(file.name)) return 'text/plain'
  return 'application/octet-stream'
}

/** Is this the member of the pair ComfyUI's canvas can import? The API file loads too on recent
 *  builds, but the UI file is the one that restores positions, groups and titles. */
export function isCanvasImportable(file: Artifact): boolean {
  return /\.json$/i.test(file.name) && !/\.api\.json$/i.test(file.name)
}

/** Fill a dragstart's DataTransfer so the drop produces a FILE. */
export function setDragPayload(dt: DataTransfer, file: Artifact): void {
  const url = fileUrl(file.path)
  try {
    dt.setData('DownloadURL', `${mimeFor(file)}:${file.name}:${url}`)
  } catch {
    /* non-Chromium: the text flavours below still carry something useful */
  }
  dt.setData('text/uri-list', url)
  dt.setData('text/plain', url)
  dt.effectAllowed = 'copy'
}

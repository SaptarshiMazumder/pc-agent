/* The input.
 *
 * Screenshots are how you show an agent what is wrong with an agent, so this window takes files
 * three ways — paste, drag-drop, and a button — because people reach for all three.
 */

import type { Attachment } from '@agentd/client'
import { useEffect, useRef, useState } from 'react'

/** `dragover` fires continuously while a drag is live, so "no dragover recently" reliably means
 *  it ended, however it ended. Counting dragenter/dragleave instead looks correct and is not:
 *  they fire per child element, and a drop landing outside the counted subtree never decrements. */
const DRAG_IDLE_MS = 700

const hasFiles = (dt: DataTransfer | null) => !!dt && Array.from(dt.types || []).includes('Files')

export function Composer({
  running,
  pending,
  onSend,
  onAbort,
  onFiles,
  onRemoveFile,
  onOpenWindow,
  openWindowLabel,
}: {
  running: boolean
  pending: Attachment[]
  onSend: (text: string) => void
  onAbort: () => void
  onFiles: (files: FileList | File[]) => void
  onRemoveFile: (index: number) => void
  /** Open the window of the agent being built. Absent when it has none — the button is then not
   *  rendered at all, rather than rendered disabled: there is nothing the user could do to enable
   *  it except ask for a window, which is a conversation, not a click. */
  onOpenWindow?: () => void
  openWindowLabel?: string
}) {
  const [text, setText] = useState('')
  const [dragging, setDragging] = useState(false)
  const areaRef = useRef<HTMLTextAreaElement>(null)
  const pickRef = useRef<HTMLInputElement>(null)
  const take = useRef(onFiles)
  take.current = onFiles

  // Window level, not a drop zone: preventDefault is REQUIRED or Electron navigates the whole
  // window to the dropped file:// URL and the UI is replaced by the image.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    const off = () => {
      if (timer) clearTimeout(timer)
      timer = null
      setDragging(false)
    }
    const on = () => {
      setDragging(true)
      if (timer) clearTimeout(timer)
      timer = setTimeout(off, DRAG_IDLE_MS)
    }
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e.dataTransfer)) return
      e.preventDefault()
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
      on()
    }
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e.dataTransfer)) return
      e.preventDefault()
      off()
      take.current(e.dataTransfer!.files)
    }
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('drop', onDrop)
    window.addEventListener('dragend', off)
    window.addEventListener('blur', off)
    return () => {
      if (timer) clearTimeout(timer)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('drop', onDrop)
      window.removeEventListener('dragend', off)
      window.removeEventListener('blur', off)
    }
  }, [])

  const submit = () => {
    if (running) return
    if (!text.trim() && !pending.length) return
    onSend(text)
    setText('')
    // Reset the auto-grown height with the value, or the box stays tall over an empty field.
    if (areaRef.current) areaRef.current.style.height = 'auto'
  }

  return (
    <div className="composer-wrap">
      <div className={`composer ${dragging ? 'drag' : ''}`}>
        {pending.length > 0 && (
          <div className="attachments">
            {pending.map((a, i) => (
              <span className="chip-file" key={`${a.name}-${i}`}>
                {a.mimeType?.startsWith('image/') && (
                  <img src={`data:${a.mimeType};base64,${a.dataBase64}`} alt={a.name} />
                )}
                <span className="chip-name">{a.name}</span>
                <button className="chip-x" title="Remove" onClick={() => onRemoveFile(i)}>
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={areaRef}
          rows={1}
          value={text}
          placeholder="Describe the agent you want…"
          onChange={(e) => {
            setText(e.target.value)
            const el = e.target
            el.style.height = 'auto'
            el.style.height = `${Math.min(el.scrollHeight, window.innerHeight * 0.4)}px`
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          onPaste={(e) => {
            // `files` covers a copied FILE, but a copied IMAGE (screenshot tool, "copy image") can
            // arrive ONLY in `items` with `files` empty — read both or pasting a screenshot
            // silently does nothing. A plain text paste falls through untouched.
            const dt = e.clipboardData
            if (!dt) return
            const fromItems = Array.from(dt.items || [])
              .filter((it) => it.kind === 'file')
              .map((it) => it.getAsFile())
              .filter((f): f is File => !!f)
            const files = dt.files && dt.files.length ? Array.from(dt.files) : fromItems
            if (!files.length) return
            e.preventDefault()
            onFiles(files)
          }}
        />

        <div className="composer-foot">
          <button
            className="icon-btn"
            title="Attach files"
            onClick={() => pickRef.current?.click()}
          >
            +
          </button>
          {/* BESIDE THE COMPOSER, because building a window and looking at it are one loop. It
              used to live only in the agentd window: build here, switch app, find the agent, open
              it, come back. */}
          {onOpenWindow && (
            <button className="open-app-btn" onClick={onOpenWindow} title="Open this agent's window">
              <span className="ico">◱</span>
              <span>{openWindowLabel || 'Open app'}</span>
            </button>
          )}
          <span className="hint">
            {running
              ? 'running…'
              : dragging
                ? 'drop to attach'
                : 'Enter to send · Shift+Enter for a new line · paste or drop images'}
          </span>
          <button
            className={`send ${running ? 'stop' : ''}`}
            title={running ? 'Stop' : 'Send'}
            onClick={() => (running ? onAbort() : submit())}
          >
            {running ? '■' : '↑'}
          </button>
        </div>

        <input
          ref={pickRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) onFiles(e.target.files)
            // Clear it, or picking the SAME file twice in a row fires no change event.
            e.target.value = ''
          }}
        />
      </div>
    </div>
  )
}

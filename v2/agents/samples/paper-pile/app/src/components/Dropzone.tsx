import { useEffect, useRef, useState } from 'react'

/** Four ways in, because people reach for all of them: drop, click, the file dialog, and PASTE.
 *  Dragging over MUST look different — a drop zone that does not react is one the user cannot
 *  tell will accept the drop, so they hover, hesitate, and use the button instead. */
export function Dropzone({ onFiles }: { onFiles: (files: FileList | File[]) => void }) {
  const [over, setOver] = useState(false)
  const picker = useRef<HTMLInputElement>(null)

  // PASTE ANYWHERE ON THE PAGE. Copying a file in the file manager and hitting Ctrl+V is how a lot
  // of people move one document, and a page that ignores the clipboard just does nothing — no
  // error, no hint that the gesture was seen.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const files = Array.from(e.clipboardData?.files ?? [])
      if (!files.length) return
      e.preventDefault()
      // A pasted screenshot arrives with NO filename. Left unnamed it is stored without an
      // extension, and nothing downstream can tell what it is.
      onFiles(files.map((f) => (f.name ? f : namedFromType(f))))
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [onFiles])

  return (
    <>
      <div
        className={`drop${over ? ' over' : ''}`}
        onClick={() => picker.current?.click()}
        onDragEnter={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragOver={(e) => {
          // Without preventDefault the browser navigates to the file and the app disappears.
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          onFiles(e.dataTransfer.files)
        }}
      >
        <span className="drop-title">Drop documents here</span>
        <span className="drop-hint">PDF, Word, slides — click to choose, or paste</span>
      </div>
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files) onFiles(e.target.files)
          // Reset, or choosing the same file twice in a row fires no change event.
          e.target.value = ''
        }}
      />
    </>
  )
}

/** Give a clipboard file a name derived from its type, so `report` never lands on disk as a
 *  file with no extension that nothing can open. */
function namedFromType(file: File): File {
  const ext = (file.type.split('/')[1] || 'bin').replace(/[^a-z0-9]/gi, '')
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  return new File([file], `pasted-${stamp}.${ext}`, { type: file.type })
}

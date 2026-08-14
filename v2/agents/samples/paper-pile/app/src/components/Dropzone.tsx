import { useRef, useState } from 'react'

/** Three ways in, because people reach for all three: drop, click, and the file dialog.
 *  Dragging over MUST look different — a drop zone that does not react is one the user cannot
 *  tell will accept the drop, so they hover, hesitate, and use the button instead. */
export function Dropzone({ onFiles }: { onFiles: (files: FileList | File[]) => void }) {
  const [over, setOver] = useState(false)
  const picker = useRef<HTMLInputElement>(null)

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
        <span className="drop-hint">PDF, Word, slides — or click to choose</span>
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

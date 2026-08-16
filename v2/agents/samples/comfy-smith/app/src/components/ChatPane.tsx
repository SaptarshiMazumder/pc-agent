/* The chat side: the transcript, the input, and the files staged between them.
 *
 * Staging lives HERE rather than in the composer because a file can arrive from three places —
 * dropped on the transcript, pasted into the box, picked from the button — and only one of them
 * is inside the composer. One owner, three doors.
 */

import { useRef, useState } from 'react'
import type { Attachment } from '@agentd/client'
import { readAttachments, type Turn } from '../agentd'
import { Composer } from './Composer'
import { Thread, type Suggestion } from './Thread'

export function ChatPane({
  turns,
  busy,
  suggestions,
  onAsk,
  onStop,
}: {
  turns: Turn[]
  busy: boolean
  suggestions: Suggestion[]
  onAsk: (text: string, attachments: Attachment[]) => void
  onStop: () => void
}) {
  const [pending, setPending] = useState<Attachment[]>([])
  const [dragging, setDragging] = useState(false)
  // dragenter/dragleave fire for every child element the cursor crosses, so a boolean flickers
  // the overlay off the moment the pointer moves over a message. Counting entries and exits is
  // the only version that stays stable.
  const depth = useRef(0)

  const take = async (files: FileList | File[]) => {
    const read = await readAttachments(files)
    if (read.length) setPending((prev) => [...prev, ...read])
  }

  const carriesFiles = (e: React.DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files')

  return (
    <section
      className="pane chat"
      onDragEnter={(e) => {
        if (!carriesFiles(e)) return
        depth.current += 1
        setDragging(true)
      }}
      onDragOver={(e) => {
        // Without preventDefault the browser navigates to the dropped file and the app is gone.
        if (carriesFiles(e)) e.preventDefault()
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1)
        if (depth.current === 0) setDragging(false)
      }}
      onDrop={(e) => {
        if (!carriesFiles(e)) return
        e.preventDefault()
        depth.current = 0
        setDragging(false)
        void take(e.dataTransfer.files)
      }}
    >
      <Thread turns={turns} suggestions={suggestions} onPick={(t) => onAsk(t, [])} />
      <Composer
        busy={busy}
        onStop={onStop}
        pending={pending}
        onFiles={(files) => void take(files)}
        onRemove={(i) => setPending((prev) => prev.filter((_, j) => j !== i))}
        onSend={(text) => {
          onAsk(text, pending)
          setPending([])
        }}
      />
      {dragging && (
        <div className="dropveil">
          <div>Drop images to attach</div>
        </div>
      )}
    </section>
  )
}

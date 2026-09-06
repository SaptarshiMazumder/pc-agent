/* Reference media — the button that sends INPUT assets to the workflow, not to the model.
 *
 * This is deliberately NOT part of the skeleton Composer. The composer's paperclip stages chat
 * attachments, which the model sees as vision (judging a render, "what's wrong here"). This sends
 * workflow INPUT — the person to animate, a start frame, a driving video — straight to the
 * ComfyUI instance via the agent's workspace, so the pixels never ride a model call. See
 * `useRun.sendReferences`.
 */

import { ImagePlus } from 'lucide-react'
import { useRef, useState } from 'react'

export function ReferenceMedia({
  onReferences,
  disabled,
}: {
  onReferences: (files: FileList | File[]) => Promise<void>
  /** While a run is going / the socket is closed: sending would race the current turn. */
  disabled: boolean
}) {
  const pickRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  const pick = async (list: FileList | null) => {
    if (!list?.length) return
    setBusy(true)
    try {
      await onReferences(list)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="refmedia">
      <button
        type="button"
        className="refmedia-btn"
        disabled={disabled || busy}
        title="Send reference image/video to the workflow — goes to the ComfyUI instance, not the chat model"
        onClick={() => pickRef.current?.click()}
      >
        <ImagePlus size={15} strokeWidth={1.8} />
        <span>{busy ? 'Adding…' : 'Add reference media'}</span>
      </button>
      <input
        ref={pickRef}
        type="file"
        accept="image/*,video/*"
        multiple
        hidden
        onChange={(e) => {
          void pick(e.target.files)
          e.target.value = '' // re-picking the same file must fire change again
        }}
      />
    </div>
  )
}

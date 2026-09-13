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
  disabledReason = '',
  queued = false,
}: {
  onReferences: (files: FileList | File[]) => Promise<void>
  /** ONLY when the upload itself cannot happen — i.e. no daemon connection.
   *
   *  It used to be `!connected || running` too, and that was the bug: adding reference media is
   *  an upload AND a message, only the message has to wait for a turn to end, and gating both on
   *  `running` killed the upload at the one moment it is most wanted. The message is queued now
   *  (see `pendingReferences`), so a run in flight is no longer a reason to refuse the click. */
  disabled: boolean
  /** WHY it is off, shown on hover. A control that greys out for more than one reason and names
   *  none of them sends people looking for the wrong fault — this button's two states were
   *  "no daemon" and "agent is busy", identical on screen, and the busy one got diagnosed as a
   *  connection problem. */
  disabledReason?: string
  /** Something is uploaded and waiting on the current turn to end before the agent is told. */
  queued?: boolean
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
        title={
          disabled && disabledReason
            ? disabledReason
            : 'Send reference image/video to the workflow — goes to the ComfyUI instance, not the chat model'
        }
        onClick={() => pickRef.current?.click()}
      >
        <ImagePlus size={15} strokeWidth={1.8} />
        <span>
          {busy ? 'Adding…' : queued ? 'Added — handing over after this turn' : 'Add reference media'}
        </span>
      </button>
      {/* WHICH DOOR IS WHICH, said once where both doors are. Two ways to hand the agent an
          image exist and they do opposite things: this button feeds the WORKFLOW (the person,
          the garment, a start frame — uploaded to the workspace, never a model call); the chat
          box feeds the MODEL's eyes (judge a render, "what's wrong here"). People used the chat
          for references and got a workflow built around a placeholder. */}
      <span className="refmedia-note">
        Reference images and videos <b>for generation</b> go here — select several at once.
        Images pasted or dropped into the chat are only looked at, never generated from.
      </span>
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

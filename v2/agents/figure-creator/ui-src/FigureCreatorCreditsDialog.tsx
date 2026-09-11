/** Product navigation only. The screen and all payment behavior remain the shared template's. */
import { useRef, useState } from 'react'
import Credits from '../../agent-builder/skills/build-agent/templates/_common/credits/Credits'

export function FigureCreatorCreditsDialog() {
  const dialog = useRef<HTMLDialogElement>(null)
  const [loaded, setLoaded] = useState(false)

  return (
    <>
      <button className="btn fc-credits-link" type="button" onClick={() => {
        setLoaded(true)
        dialog.current?.showModal()
      }}>Credits &amp; billing</button>
      <dialog ref={dialog} className="fc-billing-dialog" aria-label="Credits & billing">
        <div className="fc-billing-toolbar">
          <button className="btn" type="button" onClick={() => dialog.current?.close()}>
            Back to Figure Creator
          </button>
        </div>
        {/* Stay mounted after opening: switching back to chat must not erase a pending receipt.
            Native dialog supplies focus trapping, Escape, and restoration to the opener. */}
        {loaded && <Credits agentId="figure-creator" />}
      </dialog>
    </>
  )
}

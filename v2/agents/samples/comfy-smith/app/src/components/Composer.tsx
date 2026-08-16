import { useRef, useState } from 'react'
import type { Attachment } from '@agentd/client'

/** The input. It takes text, and it takes FILES — pasted, picked, or dropped on the transcript
 *  (the drop target lives one level up, in ChatPane, because dropping onto the conversation is
 *  the gesture people actually make).
 *
 *  Paste is the one that earns its keep: the common case is a screenshot of a broken render, and
 *  Ctrl+V is the whole interaction. Without a paste handler that image goes nowhere and the user
 *  ends up describing the picture in words. */
export function Composer({
  busy,
  pending,
  onFiles,
  onRemove,
  onSend,
  onStop,
}: {
  busy: boolean
  pending: Attachment[]
  onFiles: (files: FileList | File[]) => void
  onRemove: (index: number) => void
  onSend: (text: string) => void
  onStop: () => void
}) {
  const [text, setText] = useState('')
  const areaRef = useRef<HTMLTextAreaElement>(null)
  const pickRef = useRef<HTMLInputElement>(null)

  // Attachments alone are a valid message — an image with no caption is still a question.
  const canSend = !busy && (!!text.trim() || pending.length > 0)

  const submit = () => {
    if (!canSend) return
    onSend(text)
    setText('')
    // Reset the auto-grown height with the value, or the box stays tall over an empty field.
    if (areaRef.current) areaRef.current.style.height = 'auto'
  }

  return (
    <div className="composer">
      {pending.length > 0 && (
        <div className="chips">
          {pending.map((a, i) => (
            <span className="chip" key={`${a.name}-${i}`} title={a.name}>
              {a.mimeType?.startsWith('image/') ? (
                <img src={`data:${a.mimeType};base64,${a.dataBase64}`} alt="" />
              ) : (
                <span className="chip-doc">FILE</span>
              )}
              <span className="chip-name">{a.name}</span>
              <button className="chip-x" onClick={() => onRemove(i)} title="Remove">
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="composer-box">
        <textarea
          ref={areaRef}
          value={text}
          rows={1}
          placeholder={
            busy ? 'Type while it works — press Stop to interrupt' : 'Ask anything, or paste a render'
          }
          onChange={(e) => {
            setText(e.target.value)
            // Grow with the content up to a ceiling — a composer that expands forever pushes the
            // conversation off screen while you are still typing.
            const el = e.target
            el.style.height = 'auto'
            el.style.height = `${Math.min(el.scrollHeight, 180)}px`
          }}
          onPaste={(e) => {
            // Only intercept when the clipboard actually carries files. Copying an image out of a
            // browser puts BOTH a file and its markup on the clipboard, so pasting without this
            // check would drop the image and paste an <img> tag instead.
            const files = e.clipboardData?.files
            if (files && files.length) {
              e.preventDefault()
              onFiles(files)
            }
          }}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter breaks the line. The reverse loses a half-written message
            // to a reflex, which is unrecoverable — the box is already cleared.
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <div className="foot">
          <button className="attach" onClick={() => pickRef.current?.click()} title="Attach files">
            +
          </button>
          <span className="hint">
            {busy
              ? 'Comfy Smith is working — your message sends as soon as you stop it'
              : 'Enter to send · Shift+Enter for a new line · drop or paste images'}
          </span>
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
          {/* ONE BUTTON, TWO JOBS — never a disabled one. While the agent runs this stops it;
              the typed message stays in the box and sends on the next press. A greyed-out send
              is the state people are in exactly when they most need to say something. */}
          {busy ? (
            <button className="send stop" onClick={onStop} title="Stop">
              <span className="square" aria-hidden />
            </button>
          ) : (
            <button className="send" onClick={submit} disabled={!canSend} title="Send">
              ↑
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

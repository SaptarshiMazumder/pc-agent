/* The input.
 *
 * Screenshots are how you show an agent what is wrong with an agent, so this window takes files
 * three ways — paste, drag-drop, and a button — because people reach for all three.
 */

import { ArrowUp, Paperclip, Plus, Square, Upload } from 'lucide-react'

import type { Attachment } from '@agentd/client'
import { useEffect, useRef, useState } from 'react'

import { useApp } from '../state/store'

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
  onFork,
  forkLabel,
  forkBusy,
  meter,
  connected,
  model,
  credits,
  onCredits,
  placeholder = 'Send a message…',
  maxFiles,
}: {
  running: boolean
  pending: Attachment[]
  onSend: (text: string) => void
  onAbort: () => void
  onFiles: (files: FileList | File[]) => void
  onRemoveFile: (index: number) => void
  /** Copy this conversation and continue in the copy. Absent on an empty chat — there is
   *  nothing to fork, and a button that produces an empty duplicate is a button that lies. */
  onFork?: () => void
  /** What the button says right now: mid-fork, or the confirmation afterwards. The copy is
   *  identical to what was already on screen, so without this the click has no visible effect. */
  forkLabel?: string
  forkBusy?: boolean
  /** How full the context is. Passed in rather than read here: the composer draws the chrome,
   *  it does not decide what a token budget means. */
  meter?: React.ReactNode
  /** Is the socket open? A composer that accepts a message it cannot send is a message lost. */
  connected: boolean
  /** The model that actually ran the last step. Empty until something has run. */
  model: string
  /** What the empty composer invites. Set it to your agent's job — "Ask about a paper…",
   *  "Describe the workflow…" — because the default is deliberately generic. */
  placeholder?: string
  /** Platform credits left, or null for "we do not know" — see agentd/credits.ts. */
  credits: number | null
  onCredits: () => void
  /** The attachment cap, so the strip can SAY it. Files past it are dropped silently otherwise. */
  maxFiles: number
}) {
  const [text, setText] = useState('')
  const [dragging, setDragging] = useState(false)
  const areaRef = useRef<HTMLTextAreaElement>(null)

  /* A user message's Edit action loads its text back in here to tweak and re-send. The seed is an
     OBJECT so editing the same message twice still re-fires — see the store. Focus moves in and
     the caret goes to the end, because the point of Edit is to change what is already there. */
  const seed = useApp((s) => s.composerSeed)
  useEffect(() => {
    if (!seed) return
    setText(seed.text)
    const el = areaRef.current
    if (!el) return
    el.focus()
    requestAnimationFrame(() => el.setSelectionRange(el.value.length, el.value.length))
  }, [seed])
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
    if (running || !connected) return
    if (!text.trim() && !pending.length) return
    onSend(text)
    setText('')
    // Reset the auto-grown height with the value, or the box stays tall over an empty field.
    if (areaRef.current) areaRef.current.style.height = 'auto'
  }

  return (
    <div className="composer-wrap">
      {/* AN OVERLAY ACROSS THE WHOLE STAGE, agentd's affordance. A tinted border on the composer
          alone told you a drag was live only if you happened to be looking at the bottom of the
          window — and the drop is accepted anywhere, so that is the wrong place to say so. */}
      {dragging && (
        <div className="chat-dropzone" aria-hidden>
          <div className="chat-dropzone-inner">
            <Upload size={28} />
            <span>Drop files to attach</span>
          </div>
        </div>
      )}
      <div className="composer">
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
            {/* SAID, not just enforced. chat.ts silently drops everything past the cap, so a user
                who dropped fifteen files sees ten and no explanation. */}
            {pending.length >= maxFiles && (
              <span className="att-limit">
                <Paperclip size={11} /> Max {maxFiles} files
              </span>
            )}
          </div>
        )}

        <textarea
          ref={areaRef}
          rows={1}
          value={text}
          disabled={!connected}
          /* YOUR AGENT'S WORDS. This said "Describe the agent you want…" because the skeleton
             was lifted from the window that builds agents — so every agent made from it invited
             its user to describe an agent. Say what THIS one is for. */
          placeholder={connected ? placeholder : 'connecting…'}
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

        {/* TWO ROWS, because the things they carry answer different questions. Above: what you can
            DO with this conversation. Below: what it is running on and what it costs — agentd's
            status strip, which had nowhere to go on a single row already full of actions. */}
        <div className="composer-foot">
          <button
            className="composer-attach"
            title="Attach files"
            onClick={() => pickRef.current?.click()}
          >
            <Plus size={19} />
          </button>
          {/* BESIDE THE COMPOSER, because building a window and looking at it are one loop. It
              used to live only in the agentd window: build here, switch app, find the agent, open
              it, come back. */}
          {meter}
          {onFork && (
            <button
              className={`ghost-chip ${forkLabel && !forkBusy ? 'ok' : ''}`}
              onClick={onFork}
              disabled={forkBusy}
              title="Copy this conversation and continue in the copy"
            >
              <span className="ico">{forkBusy ? '◌' : '⑂'}</span>
              <span>{forkLabel || 'Fork'}</span>
            </button>
          )}
          <span className="grow" />
          {running ? (
            <button className="composer-send stop" title="Stop the run" onClick={onAbort}>
              <Square size={13} fill="currentColor" strokeWidth={0} />
            </button>
          ) : (
            <button
              className={`composer-send ${text.trim() || pending.length ? 'ready' : ''}`}
              title={connected ? 'Send' : 'Not connected'}
              disabled={(!text.trim() && !pending.length) || !connected}
              onClick={submit}
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>

        {/* SEPARATORS ARE THE STYLESHEET'S, not this file's — every conditional piece below used
            to carry its own ` · `, which is how a hint ends up starting with a stray dot the
            moment one of them is absent. */}
        <div className="composer-hint">
          <span className="hint-model">{model || 'no model yet'}</span>
          {credits !== null && (
            <>
              {/* A dead end is the worst place to learn you are out of credits, so the readout is
                  also the way to the top-up panel. */}
              <button
                type="button"
                className={`hint-credits ${credits === 0 ? 'empty' : ''}`}
                onClick={onCredits}
                title={
                  credits === 0
                    ? 'Out of credits — the next message will be refused. Click to top up.'
                    : 'Platform credits left on this account. Updates after each message. Click to top up.'
                }
              >
                {credits === 0 ? 'no credits left — top up' : `${credits.toLocaleString()} credits`}
              </button>
            </>
          )}
          {/* STATUS AND INSTRUCTIONS ARE DIFFERENT THINGS, so they are different elements.
              `hint-note` is what is happening right now — always worth the room. `hint-keys`
              teaches the keyboard, which a narrow window (the dashboard's agent panel) can
              drop without losing anything it could not discover by pressing Enter. */}
          {!connected ? (
            <span className="hint-note">not connected</span>
          ) : running ? (
            <span className="hint-note">running — press Stop to interrupt</span>
          ) : dragging ? (
            <span className="hint-note">drop to attach</span>
          ) : (
            <span className="hint-keys">
              Enter to send · Shift+Enter for a new line · paste or drop images
            </span>
          )}
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

/* The input.
 *
 * Screenshots are how you show an agent what is wrong with an agent, so this window takes files
 * three ways — paste, drag-drop, and a button — because people reach for all three.
 */

import { ArrowUp, Loader2, Paperclip, Plus, Square, Upload } from 'lucide-react'

import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { PendingAttachment } from '../agentd/chat'
import type { LibraryItem } from '../agentd/library'
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
  library,
  onAbort,
  onFiles,
  onRemoveFile,
  onFork,
  forkLabel,
  forkBusy,
  meter,
  connected,
  credits,
  onCredits,
  placeholder = 'Send a message…',
  maxFiles,
}: {
  running: boolean
  pending: PendingAttachment[]
  onSend: (text: string) => void
  /** The Library's catalogue, for the @ picker. Absent when there is no daemon to ask. */
  library?: () => Promise<LibraryItem[]>
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
  /* THE @ PICKER. Typing `@` opens the Library's catalogue over the box; picking an item writes
     it into the message in words — `the Library workflow "jacket-reel"` — because that is what
     the agent acts on (its library_* tools find the item by name). No token to resolve, no
     hidden id in the text, and a message that reads as a sentence if it is ever seen raw. */
  const [mention, setMention] = useState<{ start: number; end: number; query: string } | null>(null)
  const [libraryItems, setLibraryItems] = useState<LibraryItem[] | null>(null)
  const [highlight, setHighlight] = useState(0)
  const onChange = (value: string, caret: number): void => {
    setText(value)
    if (!library) return
    const before = value.slice(0, caret)
    const m = before.match(/(?:^|\s)@([\w-]*)$/)
    if (!m) {
      if (mention) setMention(null)
      return
    }
    setMention({ start: before.length - m[1].length - 1, end: caret, query: m[1] })
    setHighlight(0)
    if (libraryItems === null) {
      library()
        .then((items) => setLibraryItems(items))
        .catch(() => setLibraryItems([]))
    }
  }
  const mentionMatches = mention
    ? (libraryItems || []).filter((i) => i.name.toLowerCase().includes(mention.query.toLowerCase()))
    : []
  const pickMention = (item: LibraryItem): void => {
    if (!mention) return
    const words = `the Library ${item.kind} "${item.name}" `
    const next = text.slice(0, mention.start) + words + text.slice(mention.end)
    setText(next)
    setMention(null)
    const el = areaRef.current
    if (el) {
      const at = mention.start + words.length
      requestAnimationFrame(() => {
        el.focus()
        el.setSelectionRange(at, at)
      })
    }
  }
  const [dragging, setDragging] = useState(false)
  /* STOP WAS PRESSED, AND THE RUN HAS NOT ENDED YET.
   *
   * Abort is not instant and cannot be: the daemon signals the run and kills what it can, but a
   * tool already inside a blocking call (a big download, a render being polled) only lets go when
   * that call returns. Before this, the button simply sat there looking unpressed for as long as
   * that took, so the honest reading was "Stop is broken" — and the second and third clicks that
   * followed did nothing either.
   *
   * Cleared by `running` going false, which is the real end of the run — never by a timer, which
   * would claim it had stopped without knowing. */
  const [stopping, setStopping] = useState(false)
  useEffect(() => {
    if (!running) setStopping(false)
  }, [running])
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
  /* GROW TO FIT — KEYED ON THE VALUE, not on typing.
   *
   * This used to live inside the textarea's `onChange`, which fires only when a HUMAN types. Every
   * other way the box gets text set it without resizing: a starter prompt, the Edit action loading
   * a sent message back in, any future seed. The box stayed one row tall and clipped the rest mid
   * sentence, with the controls crammed under a half-shown line — and the one case that looked
   * fine (typing) hid it, because each keystroke happened to fix the height.
   *
   * A layout effect on `text` covers every origin by construction, including the reset to '' after
   * a send, which previously left the box expanded around nothing. `useLayoutEffect` rather than
   * `useEffect` so the measure-and-set happens before paint; with useEffect the wrong height is
   * briefly visible as a flicker on every seed.
   *
   * 'auto' first is load-bearing: `scrollHeight` reports the CONTENT height only when the element
   * is not already holding a taller explicit height, so without the reset the box could grow and
   * never shrink. */
  useLayoutEffect(() => {
    const el = areaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, window.innerHeight * 0.4)}px`
  }, [text])

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
                {a.thumbnailDataUrl && (
                  <img src={a.thumbnailDataUrl} alt="" decoding="async" />
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

        {mention && (
          <div className="mention" role="listbox" aria-label="Library items">
            {libraryItems === null ? (
              <div className="mention-note">Reading your Library…</div>
            ) : mentionMatches.length === 0 ? (
              <div className="mention-note">
                {libraryItems.length === 0 ? 'Your Library is empty' : `Nothing in the Library matches "${mention.query}"`}
              </div>
            ) : (
              mentionMatches.slice(0, 8).map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  role="option"
                  aria-selected={i === highlight}
                  className={`mention-row${i === highlight ? ' on' : ''}`}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pickMention(item)
                  }}
                >
                  <span className="mention-name">{item.name}</span>
                  <span className="mention-kind">{item.kind}</span>
                  {item.from?.title && <span className="mention-from">{item.from.title}</span>}
                </button>
              ))
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
          onChange={(e) => onChange(e.target.value, e.target.selectionStart ?? e.target.value.length)}
          onKeyDown={(e) => {
            if (mention) {
              if (e.key === 'Escape') {
                e.preventDefault()
                setMention(null)
                return
              }
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault()
                const n = Math.min(mentionMatches.length, 8)
                if (n) setHighlight((h) => (h + (e.key === 'ArrowDown' ? 1 : n - 1)) % n)
                return
              }
              if ((e.key === 'Enter' || e.key === 'Tab') && mentionMatches.length) {
                e.preventDefault()
                pickMention(mentionMatches[Math.min(highlight, mentionMatches.length - 1)])
                return
              }
            }
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
          {/* WHAT IS ANSWERING, AND WHAT IT COSTS -- on the action row rather than on a line of
              their own. They used to sit in a `composer-hint` strip below, in MONOSPACE, sharing
              a ` · ` separated sentence with three keyboard instructions; on a phone that wrapped
              to three lines and was the largest thing under an empty input.

              THE KEYBOARD HINTS ARE GONE, not moved. "Enter to send · Shift+Enter for a new line"
              is a desktop fact printed permanently under a box most people now open on a touch
              screen, where there is no Shift and Enter is whatever the on-screen keyboard says.
              It taught, once, something one press discovers. */}
          {credits !== null && (
            /* A dead end is the worst place to learn you are out of credits, so the readout is
               also the way to the top-up panel. */
            <button
              type="button"
              className={`composer-credits ${credits === 0 ? 'empty' : ''}`}
              onClick={onCredits}
              title={
                credits === 0
                  ? 'Out of credits — the next message will be refused. Click to top up.'
                  : 'Platform credits left on this account. Click to top up.'
              }
            >
              {credits === 0 ? 'no credits' : credits.toLocaleString()}
            </button>
          )}
          {/* STATUS STILL EARNS ITS ROOM -- it is what is happening now, not an instruction.
              Only ever one of these, and only when there is something to say. */}
          {stopping ? (
            <span className="composer-note">stopping…</span>
          ) : !connected ? (
            <span className="composer-note bad">not connected</span>
          ) : dragging ? (
            <span className="composer-note">drop to attach — analysis only, 5 MB each</span>
          ) : null}
          {/* BESIDE THE COMPOSER, because building a window and looking at it are one loop. */}
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
            <button
              className={`composer-send stop${stopping ? ' is-stopping' : ''}`}
              title={
                stopping
                  ? 'Stopping — waiting for the step in flight to finish'
                  : 'Stop the run'
              }
              disabled={stopping}
              onClick={() => {
                setStopping(true)
                onAbort()
              }}
            >
              {stopping ? (
                <Loader2 size={14} strokeWidth={2.2} className="ld-spin" />
              ) : (
                <Square size={13} fill="currentColor" strokeWidth={0} />
              )}
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

        {/* THE IMAGE RULE EARNS ITS LINE; the keyboard hints did not.
            "for analysis only" is not a tip, it is the answer to "why did it not use my
            reference?" -- this agent reads an attached image, it does not send it to ComfyUI
            as an input -- and 5 MB is a limit a person hits with one phone photo. Deleting
            both along with the Enter/Shift+Enter lecture threw away the only two facts on
            that strip a user could not discover by trying.

            ONE LINE, SANS, and only while the box is empty: it is a thing to learn once, and
            once you are typing you have learned it or do not need it yet. */}
        {!text.trim() && !pending.length && (
          <p className="composer-imagehint">Images: analysis only, 5 MB each</p>
        )}

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

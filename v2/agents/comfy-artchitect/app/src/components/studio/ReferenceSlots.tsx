/* The References section of the rail — one row per slot the workflow needs, filled by dropping
 * or picking a file; the files land in the chat's references folder NAMED BY ROLE, which is what
 * the agent's run tool reads (agentd/reference-slots.ts).
 *
 * WHY THE RAIL AND NOT THE COMPOSER. The composer's paperclip stages chat attachments, which the
 * model sees as vision. References are workflow INPUT: they live in the workspace, next to the
 * workflows and outputs the rail already lists, and the agent reads them off the same folder.
 * The button that used to sit above the composer put an upload beside the wrong door.
 *
 * WHAT THE PERSON SEES. Empty slots first, each with what the agent asked for, so "what is it
 * waiting on" is answered by the list itself — and a "2 of 3" count when a run is refused for a
 * missing slot. A filled slot shows the file and a Replace. Files added before any slot existed,
 * or extra ones, sit under "Other" with their real names; the agent can move one into a role
 * from chat ("the second one is the shirt" → comfy_reference_assign), or the user drops it on
 * the slot.
 */

import { ImagePlus, RefreshCw, Upload } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'

import { humanSize, type Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'

export function ReferenceSlots({
  slots,
  free,
  disabled,
  onAdd,
  onOpen,
}: {
  slots: Slot[]
  free: Artifact[]
  /** Only when the upload itself cannot happen — no daemon connection. */
  disabled: boolean
  /** Put `file` in `role` (null = keep its own name, under Other). */
  onAdd: (file: File, role: string | null) => Promise<void>
  onOpen: (a: Artifact) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const pickRef = useRef<HTMLInputElement>(null)
  const pickRole = useRef<string | null>(null)

  const add = async (files: FileList | File[] | null, role: string | null): Promise<void> => {
    const list = Array.from(files || [])
    if (!list.length || disabled) return
    setBusy(role ?? '*')
    try {
      // A slot takes ONE file; the rest go under Other rather than silently vanishing.
      if (role) {
        await onAdd(list[0], role)
        for (const f of list.slice(1)) await onAdd(f, null)
      } else {
        for (const f of list) await onAdd(f, null)
      }
    } finally {
      setBusy(null)
    }
  }

  const pick = (role: string | null): void => {
    pickRole.current = role
    pickRef.current?.click()
  }

  const drop = (role: string | null) => (e: DragEvent) => {
    e.preventDefault()
    setOver(null)
    void add(e.dataTransfer?.files || null, role)
  }
  const dragOver = (role: string | null) => (e: DragEvent) => {
    e.preventDefault()
    if (over !== role) setOver(role)
  }

  const filled = slots.filter((s) => s.file).length
  if (!slots.length && !free.length) {
    // Nothing declared yet and nothing added: one line and the button, so the door exists before
    // the agent has asked for anything — a person who already knows what they will need can
    // start here.
    // BIG, BY DESIGN. This is the one door for the workflow's inputs, and a small "Add" beside a
    // heading was missed by the very person who asked for it. Full width, primary, one line
    // saying what it takes — nothing else in the rail competes with it while there is nothing.
    return (
      <section className="refs refs-onboard" onDrop={drop(null)} onDragOver={dragOver(null)}>
        <header className="refs-head">
          <span>References</span>
        </header>
        <button
          type="button"
          className="refs-add refs-add-big"
          disabled={disabled || !!busy}
          onClick={() => pick(null)}
        >
          <ImagePlus size={22} strokeWidth={2} />
          {busy ? 'Adding…' : 'Add reference image or video'}
        </button>
        <p className="refs-empty">
          Drop files here any time. The agent asks for what it needs, and everything you add lands in
          this chat's references.
        </p>
        <FilePicker pickRef={pickRef} onPick={(files) => void add(files, pickRole.current)} />
      </section>
    )
  }

  return (
    <section className="refs">
      <header className="refs-head">
        <span>
          References
          {slots.length > 0 && (
            <span className={`refs-count${filled < slots.length ? ' is-short' : ''}`}>
              {filled} of {slots.length}
            </span>
          )}
        </span>
        <button type="button" className="refs-add" disabled={disabled || !!busy} onClick={() => pick(null)}>
          <ImagePlus size={16} strokeWidth={2} /> Add reference
        </button>
      </header>

      {slots.map((s) => (
        <div
          key={s.role}
          className={`refs-slot${s.file ? ' is-filled' : ' is-empty'}${over === s.role ? ' is-over' : ''}`}
          onDrop={drop(s.role)}
          onDragOver={dragOver(s.role)}
          onDragLeave={() => setOver(null)}
        >
          <div className="refs-slot-text">
            <span className="refs-role">@{s.role}</span>
            {s.what && <span className="refs-what">{s.what}</span>}
            {s.file ? (
              <button type="button" className="refs-file" onClick={() => onOpen(s.file as Artifact)}>
                {s.file.name}
                {s.file.size ? <span className="refs-size">{humanSize(s.file.size)}</span> : null}
              </button>
            ) : (
              <span className="refs-missing">not added yet</span>
            )}
          </div>
          <button
            type="button"
            className="refs-slot-btn"
            disabled={disabled || !!busy}
            title={s.file ? 'Replace this file' : 'Add a file for this slot, or drop one here'}
            onClick={() => pick(s.role)}
          >
            {busy === s.role ? (
              'Adding…'
            ) : s.file ? (
              <>
                <RefreshCw size={15} strokeWidth={2} /> Replace
              </>
            ) : (
              <>
                <Upload size={15} strokeWidth={2} /> Add file
              </>
            )}
          </button>
        </div>
      ))}

      {free.length > 0 && (
        <div className="refs-free">
          <span className="refs-sub">Other{slots.length ? ' — not in a slot yet' : ''}</span>
          {free.map((a) => (
            <button key={a.path} type="button" className="refs-file" onClick={() => onOpen(a)}>
              {a.name}
              {a.size ? <span className="refs-size">{humanSize(a.size)}</span> : null}
            </button>
          ))}
        </div>
      )}
      <FilePicker pickRef={pickRef} onPick={(files) => void add(files, pickRole.current)} />
    </section>
  )
}

function FilePicker({
  pickRef,
  onPick,
}: {
  pickRef: React.RefObject<HTMLInputElement>
  onPick: (files: FileList | null) => void
}) {
  return (
    <input
      ref={pickRef}
      type="file"
      accept="image/*,video/*,audio/*"
      multiple
      hidden
      onChange={(e) => {
        onPick(e.target.files)
        e.target.value = '' // re-picking the same file must fire change again
      }}
    />
  )
}

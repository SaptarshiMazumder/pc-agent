/* The References section of the rail — one row per slot the workflow needs, filled by dropping
 * or picking a file; the files land in the chat's references folder NAMED BY ROLE, which is what
 * the agent's run tool reads (agentd/reference-slots.ts).
 *
 * WHY THE RAIL AND NOT THE COMPOSER. The composer's paperclip stages chat attachments, which the
 * model sees as vision. References are workflow INPUT: they live in the workspace, next to the
 * workflows and outputs the rail already lists, and the agent reads them off the same folder.
 * The button that used to sit above the composer put an upload beside the wrong door.
 *
 * THE AGENT DECLARES THE DOORS; THIS PANEL HAS NONE OF ITS OWN. There is no standing "Add
 * reference" button, and the section does not render at all until there is a slot or a file —
 * because asking for an input is something the agent does, by declaring a slot, and a permanent
 * upload button next to that is a second answer to a question already answered. It also cost
 * what little width the rail has: a nowrap button sharing `justify-content: space-between` with
 * the heading squeezed the "1 of 1" count to nothing and stacked it one character per line.
 *
 * DROPPING STILL WORKS ANYWHERE IN THE PANEL, on a slot or between them — an affordance with no
 * chrome, so it costs no space and competes with nothing. A drop on a slot stops there
 * (`stopPropagation`) rather than also bubbling to the panel and landing the same file a second
 * time under "Other".
 *
 * WHAT THE PERSON SEES. Empty slots first, each with what the agent asked for, so "what is it
 * waiting on" is answered by the list itself — and a "2 of 3" count when a run is refused for a
 * missing slot. A filled slot shows the file and a Replace. Extra files sit under "Other" with
 * their real names; the agent can move one into a role from chat ("the second one is the shirt"
 * → comfy_reference_assign), or the user drops it on the slot.
 */

import { Library, RefreshCw, Upload } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'

import { humanSize, type Artifact } from '../../agentd/artifacts'
import type { Slot } from '../../agentd/reference-slots'

export function ReferenceSlots({
  slots,
  free,
  disabled,
  onAdd,
  onOpen,
  onFromLibrary,
}: {
  slots: Slot[]
  free: Artifact[]
  /** Only when the upload itself cannot happen — no daemon connection. */
  disabled: boolean
  /** Put `file` in `role` (null = keep its own name, under Other). */
  onAdd: (file: File, role: string | null) => Promise<void>
  onOpen: (a: Artifact) => void
  /** The second door on a slot: pick the file from the Library instead of this computer. */
  onFromLibrary?: (role: string) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  /* THE PANEL'S OWN DRAG STATE, for a drop that is not on any slot. `over` holds the ROLE being
     dragged onto, and the panel has no role — `dragOver(null)` would therefore CLEAR the
     highlight instead of setting one, so the biggest drop target here would be the only one that
     never acknowledged a drag. A separate flag rather than a sentinel role, because null already
     means "none". */
  const [overPanel, setOverPanel] = useState(false)
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
    // STOPS HERE. The panel is a drop target too, so without this a file dropped on a slot lands
    // twice — once in the role, once again under "Other" as the event bubbles.
    e.stopPropagation()
    setOver(null)
    setOverPanel(false)
    void add(e.dataTransfer?.files || null, role)
  }
  const dragOver = (role: string | null) => (e: DragEvent) => {
    e.preventDefault()
    // Same reason: while a slot is lit, the panel behind it must not light up as well.
    e.stopPropagation()
    if (over !== role) setOver(role)
  }

  const filled = slots.filter((s) => s.file).length
  /* NOTHING DECLARED AND NOTHING ADDED => NO SECTION AT ALL, not an empty one inviting an upload.
     The rail simply starts at the file tree until the agent asks for something. This owns its own
     border-bottom, so the separator leaves with it rather than dangling above the tree. */
  if (!slots.length && !free.length) return null

  return (
    <section
      className={`refs${overPanel ? ' is-over' : ''}`}
      onDrop={drop(null)}
      onDragOver={(e) => {
        e.preventDefault()
        if (!overPanel) setOverPanel(true)
      }}
      onDragLeave={() => setOverPanel(false)}
    >
      <header className="refs-head">
        <span className="refs-heading">
          <span className="refs-title">References</span>
          {slots.length > 0 && (
            <span className={`refs-count${filled < slots.length ? ' is-short' : ''}`}>
              {filled} of {slots.length}
            </span>
          )}
        </span>
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
          {onFromLibrary && (
            <button
              type="button"
              className="refs-slot-btn refs-slot-lib"
              disabled={disabled || !!busy}
              title="Fill this slot from your Library"
              onClick={() => onFromLibrary(s.role)}
            >
              <Library size={15} strokeWidth={2} />
            </button>
          )}
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

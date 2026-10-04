/* What the user selected, above the composer: the next message is about these files, and carries
 * their exact paths (selectionBlock), so "fix the hand", "3 more like this", "another video from
 * this" or "extend it" act on exactly what is shown here. */

import { Film, X } from 'lucide-react'

import { useApp } from '../state/store'

export function SelectionChips() {
  const selection = useApp((s) => s.selection)
  const unselect = useApp((s) => s.unselect)
  const clear = useApp((s) => s.clearSelection)
  if (!selection.length) return null
  return (
    <div className="sel-strip">
      <span className="sel-label">Selected</span>
      {selection.map((s) => (
        <span key={s.path} className="sel-chip" title={s.path}>
          {s.kind === 'video' ? (
            <span className="sel-thumb video">
              <Film size={13} />
            </span>
          ) : (
            <img className="sel-thumb" src={s.src} alt="" />
          )}
          <span className="sel-name">
            {s.shot ? `${s.shot} · ` : ''}
            {s.path.split('/').pop()}
          </span>
          <button className="chip-x" title="Unselect" onClick={() => unselect(s.path)}>
            <X size={11} />
          </button>
        </span>
      ))}
      <button className="sel-clear" onClick={clear}>
        Clear
      </button>
    </div>
  )
}

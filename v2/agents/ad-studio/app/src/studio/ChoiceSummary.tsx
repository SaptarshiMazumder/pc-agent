/* A new-ad choice once it is made: one line — what was chosen — and Change, which opens the full
 * choice again. Keeps the screen to what is still left to decide. */

import { Check, Pencil } from 'lucide-react'

export function ChoiceSummary({ n, label, value, onChange }: { n: number; label: string; value: string; onChange: () => void }) {
  return (
    <div className="choice-summary">
      <span className="new-ad-n done">
        <Check size={11} strokeWidth={3} />
      </span>
      <span className="choice-summary-label">{label}</span>
      <span className="choice-summary-value">{value}</span>
      <button className="ref-add" onClick={onChange} title={`Choose another ${label.toLowerCase()}`} aria-label={`Change step ${n}: ${label}`}>
        <Pencil size={11} /> Change
      </button>
    </div>
  )
}

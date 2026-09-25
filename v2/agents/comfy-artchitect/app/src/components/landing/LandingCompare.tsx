/* Comfy Penguin vs other AI video apps — five rows, three short cells each.
 *
 * WHY A TABLE. The old "doing it yourself / with Penguin" lists compared Penguin with ComfyUI by
 * hand, which is not what a visitor is choosing between; they are choosing between AI apps. A
 * table puts each difference on one line, where it can be read without a second pass.
 * Competitors are not named — the rows are facts about the category.
 */

import { Check, X } from 'lucide-react'

export function LandingCompare({ rows }: { rows: [string, string, string][] }): JSX.Element {
  return (
    <div className="lp-cmp" role="table" aria-label="Comfy Penguin compared with other AI video apps">
      <div className="lp-cmp-row is-head" role="row">
        <span role="columnheader" />
        <span role="columnheader">Other AI video apps</span>
        <span role="columnheader" className="is-us">Comfy Penguin</span>
      </div>
      {rows.map(([what, them, us]) => (
        <div key={what} className="lp-cmp-row" role="row">
          <span role="rowheader" className="lp-cmp-what">{what}</span>
          <span role="cell" className="lp-cmp-them">
            <X size={14} strokeWidth={2.4} aria-hidden="true" /> {them}
          </span>
          <span role="cell" className="lp-cmp-us">
            <Check size={14} strokeWidth={2.6} aria-hidden="true" /> {us}
          </span>
        </div>
      ))}
    </div>
  )
}

export default LandingCompare

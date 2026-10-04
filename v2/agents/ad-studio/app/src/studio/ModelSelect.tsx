/* One model dropdown: label and price. The lists are the agent's model specs (generation_models),
 * so a model added there shows up here. */

import type { GenModel } from '../agentd/campaigns'

export function ModelSelect({
  label,
  value,
  models,
  price = (m) => m.price,
  disabled,
  onChange,
}: {
  label: string
  value: string
  models: GenModel[]
  /** What price to show for a model in this dropdown (an edit's, an extension's…). */
  price?: (m: GenModel) => string
  disabled?: boolean
  onChange: (id: string) => void
}) {
  return (
    <label className="model-field grow">
      <span className="strip-label">{label}</span>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
            {price(m) ? ` — ${price(m)}` : ''}
          </option>
        ))}
      </select>
    </label>
  )
}

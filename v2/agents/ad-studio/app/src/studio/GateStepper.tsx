/* The recipe's gates as a row of steps: done, the one it waits at, still to come. Only the gates
 * this campaign's plan stops at are drawn (a run with no shoot sheet shows no sheet step). */

import { GATE_LABEL, GATE_ORDER, type Gate } from '../agentd/campaigns'

export function GateStepper({ gate, gates, hasSheet }: { gate: Gate; gates: string[]; hasSheet: boolean }) {
  const steps = GATE_ORDER.filter((g) => g === 'done' || gates.includes(g) || (g === 'sheet' && hasSheet))
  const at = steps.indexOf(gate)
  return (
    <ol className="stepper">
      {steps.map((g, i) => (
        <li key={g} className={`step${i < at ? ' past' : ''}${i === at ? ' now' : ''}`}>
          <span className="step-num">{i + 1}</span>
          <span className="step-label">{GATE_LABEL[g]}</span>
        </li>
      ))}
    </ol>
  )
}

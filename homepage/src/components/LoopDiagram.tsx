import { BrainCircuit, Wrench, HardDrive, Radio, RotateCcw } from 'lucide-react'

const STAGES = [
  {
    icon: BrainCircuit,
    label: 'The model',
    body: 'Any model you point it at. It picks the next move.',
  },
  {
    icon: Wrench,
    label: 'Tool calls',
    body: 'Validated against a schema before anything runs.',
  },
  {
    icon: HardDrive,
    label: 'Your machine',
    body: 'The file gets read. The command actually runs.',
  },
  {
    icon: Radio,
    label: 'Results back',
    body: 'Streamed to you live, and fed to the model to continue.',
  },
]

export function LoopDiagram() {
  return (
    <div className="loop">
      <ol className="loop__track">
        {STAGES.map((stage, index) => (
          <li key={stage.label} className="loop__node">
            <span className="loop__index">{index + 1}</span>
            <span className="loop__icon">
              <stage.icon size={19} strokeWidth={1.75} />
            </span>
            <h3 className="loop__label">{stage.label}</h3>
            <p className="loop__body">{stage.body}</p>
          </li>
        ))}
      </ol>

      <div className="loop__return">
        <RotateCcw size={15} strokeWidth={2} aria-hidden="true" />
        <span>
          Still not done? It goes around again — and if the model stalls, plans without acting, or
          answers thin, the daemon catches it and pushes it back into the loop.
        </span>
      </div>
    </div>
  )
}

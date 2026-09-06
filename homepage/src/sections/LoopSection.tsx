import { Reveal } from '../components/Reveal'
import { LoopDiagram } from '../components/LoopDiagram'

export function LoopSection() {
  return (
    <section className="section section--tint" id="loop">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">The loop</p>
          <h2>Not a chatbot that suggests. An agent that goes and does it.</h2>
          <p className="lede">
            Every turn is the same honest cycle, running as fast as your machine will go — and you
            watch each step stream past as it happens, not after.
          </p>
        </Reveal>

        <Reveal delay={80}>
          <LoopDiagram />
        </Reveal>
      </div>
    </section>
  )
}

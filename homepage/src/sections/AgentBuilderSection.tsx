import { MessagesSquare, Hammer, PlayCircle, PackageCheck } from 'lucide-react'
import { Reveal } from '../components/Reveal'

const STEPS = [
  {
    icon: MessagesSquare,
    title: 'Describe it',
    body: 'Say what the agent should do and who it is for. The builder plans before it writes a line.',
  },
  {
    icon: Hammer,
    title: 'It writes the agent',
    body: 'Manifest, identity, operating rules, skills, private Python tools, and a React window — generated, not glued.',
  },
  {
    icon: PlayCircle,
    title: 'It runs what it wrote',
    body: 'It drives the new window in a real browser, screenshots it, and fixes what it finds before showing you.',
  },
  {
    icon: PackageCheck,
    title: 'Ship it',
    body: 'Packed into a signed bundle, ready to install on another machine or publish to the registry.',
  },
]

export function AgentBuilderSection() {
  return (
    <section className="section" id="builder">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">Agent Builder</p>
          <h2>An agent whose job is building agents.</h2>
          <p className="lede">
            You will not write the boilerplate, and you will not hand-write a UI. You describe the
            job; the builder produces a working agent and proves it works by running it.
          </p>
        </Reveal>

        <div className="builder__steps">
          {STEPS.map((step, index) => (
            <Reveal key={step.title} delay={index * 70}>
              <article className="builder__step">
                <span className="builder__step-icon">
                  <step.icon size={20} strokeWidth={1.7} />
                </span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </article>
            </Reveal>
          ))}
        </div>

        <Reveal delay={80}>
          <article className="case">
            <div className="case__copy">
              <span className="chip chip--accent">Built with it</span>
              <h3 className="case__title">Comfy Artchitect</h3>
              <p className="case__body">
                It connects to your live ComfyUI box and reads what is <em>actually</em> installed —
                then researches the models, installs the ones you are missing, emits the graph in
                both the runnable and the importable format, runs it, and repairs it from the
                server's own error payload. It never recites a workflow from memory.
              </p>
              <ul className="case__facts">
                <li>
                  <strong>12</strong>
                  <span>purpose-built tools</span>
                </li>
                <li>
                  <strong>0</strong>
                  <span>shell access — it is web-safe</span>
                </li>
                <li>
                  <strong>1</strong>
                  <span>folder, window and all</span>
                </li>
              </ul>
            </div>

            <div className="case__panel" aria-hidden="true">
              <div className="case__panel-head">
                <span className="case__panel-title">Studio</span>
                <span className="case__panel-live">live</span>
              </div>
              <div className="case__kpis">
                <div>
                  <span className="case__kpi-val">24.6 GB</span>
                  <span className="case__kpi-key">VRAM free</span>
                </div>
                <div>
                  <span className="case__kpi-val">41</span>
                  <span className="case__kpi-key">models found</span>
                </div>
                <div>
                  <span className="case__kpi-val">00:38</span>
                  <span className="case__kpi-key">run elapsed</span>
                </div>
              </div>
              <div className="case__bar">
                <span className="case__bar-fill" />
              </div>
              <div className="case__gallery">
                <span />
                <span />
                <span />
                <span />
              </div>
            </div>
          </article>
        </Reveal>
      </div>
    </section>
  )
}

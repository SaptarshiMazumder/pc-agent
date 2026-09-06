import { Reveal } from '../components/Reveal'
import { AgentFileTree } from '../components/AgentFileTree'

const POINTS = [
  {
    title: 'No registration step',
    body: 'Drop the folder in. The daemon reads it. There is no database row to create and no build to run first.',
  },
  {
    title: 'It carries its own everything',
    body: 'Its identity, its rules, its playbooks, its private Python tools, and its window all live in the same folder.',
  },
  {
    title: 'Fenced by its own manifest',
    body: 'agent.toml says which tools it may call and where on disk it may write. What is not listed cannot be reached.',
  },
  {
    title: 'Zip it and it travels',
    body: 'One command packs the folder into a signed bundle someone else can install — tools, window, and all.',
  },
]

export function AgentAnatomySection() {
  return (
    <section className="section section--tint" id="anatomy">
      <div className="shell anatomy">
        <div className="anatomy__copy">
          <Reveal className="section-head">
            <p className="eyebrow">Anatomy</p>
            <h2>An agent is just a directory.</h2>
            <p className="lede">
              That is the whole abstraction. If you can make a folder, you can make an agent — and
              you can read every part of one without asking anybody what it does.
            </p>
          </Reveal>

          <dl className="anatomy__points">
            {POINTS.map((point, index) => (
              <Reveal key={point.title} delay={index * 60}>
                <dt>{point.title}</dt>
                <dd>{point.body}</dd>
              </Reveal>
            ))}
          </dl>
        </div>

        <Reveal delay={100} className="anatomy__tree">
          <AgentFileTree />
        </Reveal>
      </div>
    </section>
  )
}

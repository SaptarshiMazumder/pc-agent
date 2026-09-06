import { AppWindow } from 'lucide-react'
import { Reveal } from '../components/Reveal'
import { AGENTS } from '../data/agents'
import { iconFor } from '../lib/icons'

export function GallerySection() {
  return (
    <section className="section section--tint" id="gallery">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">In the box</p>
          <h2>A cast of agents, each with one job.</h2>
          <p className="lede">
            Some answer in chat. Some open their own window. Some never wait to be asked — they wake
            on a schedule and have the answer ready when you are.
          </p>
        </Reveal>

        <div className="gallery">
          {AGENTS.map((agent, index) => {
            const Icon = iconFor(agent.icon)
            return (
              <Reveal key={agent.id} delay={Math.min(index, 5) * 55}>
                <article className={`card card--hover agent ${agent.featured ? 'is-featured' : ''}`}>
                  <header className="agent__head">
                    <span className="agent__icon">
                      <Icon size={19} strokeWidth={1.7} />
                    </span>
                    <h3 className="agent__name">{agent.name}</h3>
                    {agent.window && (
                      <span className="agent__window" title="Ships its own window app">
                        <AppWindow size={14} strokeWidth={1.9} />
                      </span>
                    )}
                  </header>
                  <p className="agent__tagline">{agent.tagline}</p>
                  <ul className="agent__tags">
                    {agent.tags.map((tag) => (
                      <li key={tag} className="chip">
                        {tag}
                      </li>
                    ))}
                  </ul>
                </article>
              </Reveal>
            )
          })}
        </div>
      </div>
    </section>
  )
}

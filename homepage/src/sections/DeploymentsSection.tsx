import { Laptop, CloudCog, Globe2 } from 'lucide-react'
import { Reveal } from '../components/Reveal'

const MODES = [
  {
    icon: Laptop,
    name: 'Desktop, your keys',
    summary: 'Everything on your machine. Model calls go straight to the provider with your own key.',
    points: ['Tools run locally', 'Your API key, your bill', 'No account required'],
  },
  {
    icon: CloudCog,
    name: 'Desktop, our keys',
    summary:
      'Same daemon, same local tools. Only the model call is routed through the platform and metered.',
    points: ['Tools still run locally', 'No API key to manage', 'Pay in credits as you go'],
    featured: true,
  },
  {
    icon: Globe2,
    name: 'The web app',
    summary: 'The identical daemon image, hosted. Nothing to install — open a tab and start.',
    points: ['Zero install', 'Agents served as apps', 'Your workspace persists'],
  },
]

export function DeploymentsSection() {
  return (
    <section className="section" id="run">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">Where it runs</p>
          <h2>One daemon. Three ways to run it.</h2>
          <p className="lede">
            The same engine ships in all three — so an agent built on your laptop behaves the same
            way in the browser. Move between them whenever it suits you.
          </p>
        </Reveal>

        <div className="modes">
          {MODES.map((mode, index) => (
            <Reveal key={mode.name} delay={index * 70}>
              <article className={`card mode ${mode.featured ? 'is-featured' : ''}`}>
                {mode.featured && <span className="mode__flag">Most popular</span>}
                <span className="mode__icon">
                  <mode.icon size={21} strokeWidth={1.7} />
                </span>
                <h3 className="mode__name">{mode.name}</h3>
                <p className="mode__summary">{mode.summary}</p>
                <ul className="mode__points">
                  {mode.points.map((point) => (
                    <li key={point}>{point}</li>
                  ))}
                </ul>
              </article>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  )
}

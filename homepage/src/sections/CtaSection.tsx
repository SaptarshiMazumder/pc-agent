import { Download, Globe2 } from 'lucide-react'
import { Reveal } from '../components/Reveal'
import { Aurora } from '../components/Aurora'

export function CtaSection() {
  return (
    <section className="cta" id="get">
      <Aurora />
      <div className="shell">
        <Reveal className="cta__inner">
          <h2 className="cta__title">Stop pasting your files into a chat box.</h2>
          <p className="cta__lede">
            Install it once and the agent is already where your work is. Bring your own key, or
            start on ours.
          </p>
          <div className="cta__actions">
            <a href="#get" className="btn btn--primary">
              <Download size={17} />
              Download for Windows
            </a>
            <a href="#get" className="btn btn--ghost">
              <Globe2 size={17} />
              Open the web app
            </a>
          </div>
          <p className="cta__fine">
            Free to try. Your keys and your files stay on your machine.
          </p>
        </Reveal>
      </div>
    </section>
  )
}

import { PenTool, FileSignature, ShieldCheck } from 'lucide-react'
import { Reveal } from '../components/Reveal'

const CHAIN = [
  {
    icon: PenTool,
    title: 'You build it',
    body: 'In the desktop builder, or in the browser one — either way you end up with a folder.',
  },
  {
    icon: FileSignature,
    title: 'It gets signed',
    body: 'Publishing hands your bundle to a service that holds the signing key. Nothing else can write to the registry.',
  },
  {
    icon: ShieldCheck,
    title: 'It gets verified',
    body: 'Every install checks the hash and the publisher signature against a roster of trusted keys — and refuses if either fails.',
  },
]

export function RegistrySection() {
  return (
    <section className="section section--tint" id="registry">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">Publishing</p>
          <h2>Build agents. Ship them signed.</h2>
          <p className="lede">
            An agent can read your files and run your shell — so trust cannot be a checkbox. The
            whole chain from your folder to someone else's machine is signed end to end, and fails
            closed.
          </p>
        </Reveal>

        <div className="chain">
          {CHAIN.map((link, index) => (
            <Reveal key={link.title} delay={index * 80}>
              <article className="chain__link">
                <span className="chain__icon">
                  <link.icon size={19} strokeWidth={1.7} />
                </span>
                <div>
                  <h3>{link.title}</h3>
                  <p>{link.body}</p>
                </div>
              </article>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  )
}

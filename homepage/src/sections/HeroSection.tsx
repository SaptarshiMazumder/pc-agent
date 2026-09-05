import { Download, ArrowDown, KeyRound, Cpu, FolderLock } from 'lucide-react'
import { Aurora } from '../components/Aurora'
import { TerminalDemo } from '../components/TerminalDemo'

const TRUST = [
  { icon: Cpu, text: 'Any model — Gemini, Claude, GPT, local' },
  { icon: KeyRound, text: 'Your keys never leave the machine' },
  { icon: FolderLock, text: 'Your files are never uploaded' },
]

export function HeroSection() {
  return (
    <section className="hero" id="top">
      <Aurora />
      <div className="shell hero__inner">
        <div className="hero__copy">
          <p className="eyebrow">Runs on your machine</p>
          <h1 className="hero__title">
            Give an AI your <em>actual</em> computer.
          </h1>
          <p className="hero__lede">
            agentd runs the loop locally: the model asks for a tool, the tool really runs, the result
            comes straight back. Real files, a real shell, a real browser — on the machine your work
            is already on.
          </p>

          <div className="hero__actions">
            <a href="#get" className="btn btn--primary">
              <Download size={17} />
              Download for Windows
            </a>
            <a href="#loop" className="btn btn--ghost">
              <ArrowDown size={17} />
              See how it works
            </a>
          </div>

          <ul className="hero__trust">
            {TRUST.map((item) => (
              <li key={item.text}>
                <item.icon size={15} strokeWidth={1.9} aria-hidden="true" />
                {item.text}
              </li>
            ))}
          </ul>
        </div>

        <div className="hero__demo">
          <TerminalDemo />
        </div>
      </div>
    </section>
  )
}

import { Reveal } from '../components/Reveal'
import { TOOL_GROUPS } from '../data/tools'
import { iconFor } from '../lib/icons'

export function ToolsSection() {
  return (
    <section className="section" id="tools">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">Real tools</p>
          <h2>Every tool is the real one.</h2>
          <p className="lede">
            Nothing here is a sandboxed imitation. When the agent reads a file it is your file; when
            it opens a browser a browser opens.
          </p>
        </Reveal>

        <div className="tools">
          {TOOL_GROUPS.map((group, index) => {
            const Icon = iconFor(group.icon)
            return (
              <Reveal key={group.title} delay={Math.min(index, 5) * 55}>
                <article className={`card card--hover tool ${group.optIn ? 'tool--optin' : ''}`}>
                  <span className="tool__icon">
                    <Icon size={20} strokeWidth={1.7} />
                  </span>
                  <h3 className="tool__title">
                    {group.title}
                    {group.optIn && <span className="tool__badge">opt-in</span>}
                  </h3>
                  <p className="tool__body">{group.body}</p>
                  <ul className="tool__names">
                    {group.tools.map((name) => (
                      <li key={name}>
                        <code>{name}</code>
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

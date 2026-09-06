import { NakamaMark } from './NakamaMark'

const COLUMNS = [
  {
    title: 'Product',
    links: [
      { label: 'How it works', href: '#loop' },
      { label: 'Tools', href: '#tools' },
      { label: 'Agent Builder', href: '#builder' },
      { label: 'Where it runs', href: '#run' },
    ],
  },
  {
    title: 'Agents',
    links: [
      { label: 'Comfy Artchitect', href: '#builder' },
      { label: 'The gallery', href: '#gallery' },
      { label: 'Marketplace', href: '#registry' },
    ],
  },
  {
    title: 'Get started',
    links: [
      { label: 'Download for Windows', href: '#get' },
      { label: 'Open the web app', href: '#get' },
      { label: 'Read the docs', href: '#get' },
    ],
  },
]

export function SiteFooter() {
  return (
    <footer className="footer">
      <div className="shell footer__inner">
        <div className="footer__brand">
          <a href="#top" className="footer__mark" aria-label="agentd — home">
            <NakamaMark size={30} />
            <span className="nav__wordmark">agentd</span>
          </a>
          <p className="footer__blurb">
            A personal AI agent that acts on your own machine. Any model, real tools, and the files
            that were already yours.
          </p>
        </div>

        {COLUMNS.map((column) => (
          <div key={column.title} className="footer__col">
            <h3 className="footer__col-title">{column.title}</h3>
            <ul>
              {column.links.map((link) => (
                <li key={link.label}>
                  <a href={link.href}>{link.label}</a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="shell footer__base">
        <span>© {new Date().getFullYear()} agentd</span>
        <span className="footer__note">Runs on your machine. Your keys, your files.</span>
      </div>
    </footer>
  )
}

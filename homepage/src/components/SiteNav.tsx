import { useEffect, useState } from 'react'
import { Moon, Sun, Menu, X, Download } from 'lucide-react'
import { NakamaMark } from './NakamaMark'
import { useTheme } from '../lib/useTheme'

const LINKS = [
  { href: '#loop', label: 'How it works' },
  { href: '#tools', label: 'Tools' },
  { href: '#anatomy', label: 'Agents' },
  { href: '#builder', label: 'Agent Builder' },
  { href: '#run', label: 'Where it runs' },
]

export function SiteNav() {
  const [theme, toggleTheme] = useTheme()
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Close the mobile sheet on Escape, and stop the page scrolling behind it.
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [menuOpen])

  return (
    <header className={`nav ${scrolled ? 'nav--scrolled' : ''}`}>
      <div className="shell nav__inner">
        <a href="#top" className="nav__brand" aria-label="agentd — home">
          <NakamaMark size={30} />
          <span className="nav__wordmark">agentd</span>
        </a>

        <nav className="nav__links" aria-label="Sections">
          {LINKS.map((link) => (
            <a key={link.href} href={link.href} className="nav__link">
              {link.label}
            </a>
          ))}
        </nav>

        <div className="nav__actions">
          <button
            type="button"
            className="nav__icon-btn"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>

          <a href="#get" className="btn btn--primary nav__cta">
            <Download size={16} />
            Download
          </a>

          <button
            type="button"
            className="nav__icon-btn nav__burger"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <div className="nav__sheet">
          {LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="nav__sheet-link"
              onClick={() => setMenuOpen(false)}
            >
              {link.label}
            </a>
          ))}
          <a href="#get" className="btn btn--primary" onClick={() => setMenuOpen(false)}>
            <Download size={16} />
            Download
          </a>
        </div>
      )}
    </header>
  )
}

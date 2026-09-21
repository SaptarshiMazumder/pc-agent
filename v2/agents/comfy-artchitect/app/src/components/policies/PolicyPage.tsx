/* About, Contact and the policies — read inside the app, like any other screen.
 *
 * ONE SOURCE, TWO READERS. The pages are plain HTML files shipped in ui/ (about.html and the
 * rest), because a payment gateway's reviewer and a signed-out visitor must be able to read
 * them with no JavaScript and no account. A signed-in person should not be thrown into a new
 * tab with a different design to read the same words. So this screen FETCHES the same file,
 * lifts its <main>, and renders it in the app's own type and colours. Change the file, both
 * readers change.
 *
 * LINKS STAY IN THE ROOM. Every page ends with the same footer linking the other five; a click
 * on one of those swaps the page in place. Only links that leave the site (mailto:, https://)
 * behave as links.
 *
 * TRUSTED MARKUP, BY CONSTRUCTION. What is injected is a file this app ships beside itself,
 * served by the same daemon from the same folder — never user content, never a remote page.
 * The fetch is same-origin and relative, so it resolves under /apps/<id>/ on the web and under
 * the daemon's app route on a desktop alike.
 */

import './policies.css'

import { useEffect, useRef, useState, type MouseEvent } from 'react'

export type PolicyFile =
  | 'about.html'
  | 'contact.html'
  | 'terms.html'
  | 'privacy.html'
  | 'refund.html'
  | 'delivery.html'

const KNOWN: ReadonlySet<string> = new Set<PolicyFile>([
  'about.html',
  'contact.html',
  'terms.html',
  'privacy.html',
  'refund.html',
  'delivery.html',
])

const TITLES: Record<PolicyFile, string> = {
  'about.html': 'About us',
  'contact.html': 'Contact us',
  'terms.html': 'Terms & Conditions',
  'privacy.html': 'Privacy Policy',
  'refund.html': 'Refunds & Cancellation',
  'delivery.html': 'Delivery',
}

/** The <main> of one shipped page, or the reason it could not be read. */
async function loadMain(file: PolicyFile): Promise<string> {
  const res = await fetch(file, { cache: 'no-cache' })
  if (!res.ok) throw new Error(`could not load ${file} (HTTP ${res.status})`)
  const doc = new DOMParser().parseFromString(await res.text(), 'text/html')
  const main = doc.querySelector('main')
  if (!main) throw new Error(`${file} has no <main>`)
  return main.innerHTML
}

export default function PolicyPage({ start }: { start: PolicyFile }) {
  const [file, setFile] = useState<PolicyFile>(start)
  const [html, setHtml] = useState('')
  const [error, setError] = useState('')
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let stale = false
    setError('')
    loadMain(file)
      .then((h) => {
        if (stale) return
        setHtml(h)
        scroller.current?.scrollTo({ top: 0 })
      })
      .catch((e) => !stale && setError(String((e as Error)?.message || e)))
    return () => {
      stale = true
    }
  }, [file])

  /** A click on one of our own page links swaps the page here instead of navigating. */
  function onClick(e: MouseEvent<HTMLDivElement>): void {
    const a = (e.target as HTMLElement).closest('a')
    if (!a) return
    const href = a.getAttribute('href') || ''
    if (href === './' || href === 'index.html') {
      e.preventDefault()
      return
    }
    if (KNOWN.has(href)) {
      e.preventDefault()
      setFile(href as PolicyFile)
    }
  }

  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">{TITLES[file]}</h1>
          <p className="page-sub">Comfy Penguin</p>
        </div>
      </header>
      <div className="stage pol-stage">
        <div className="stage-main pol-scroll" ref={scroller}>
          {error ? (
            <p className="pol-error">{error}</p>
          ) : (
            <article className="pol" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
          )}
        </div>
      </div>
    </>
  )
}

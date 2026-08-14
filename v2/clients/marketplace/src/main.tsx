import React from 'react'
import { createRoot } from 'react-dom/client'

// The same vendored fonts the app uses, for the same reason: a strict CSP forbids remote fonts,
// and a marketplace that waits on a font CDN to draw its first card is a marketplace people leave.
import '@fontsource/bricolage-grotesque/600.css'
import '@fontsource/bricolage-grotesque/700.css'
import '@fontsource/hanken-grotesk/400.css'
import '@fontsource/hanken-grotesk/500.css'
import '@fontsource/hanken-grotesk/600.css'

import '@ui/styles.css'

import App from './App'

/**
 * THEME, without the app's store.
 *
 * The app persists a chosen theme and applies it before first paint. This page has no settings and
 * no account, so there is nothing to persist: it follows the visitor's own system preference,
 * through the same `data-theme` attribute every rule in styles.css is written against. A visitor
 * who flips their OS to dark sees this page follow, live.
 */
function followSystemTheme(): void {
  const query = matchMedia('(prefers-color-scheme: dark)')
  const apply = (): void => {
    document.documentElement.dataset.theme = query.matches ? 'dark' : 'light'
  }
  apply()
  query.addEventListener('change', apply)
}

followSystemTheme()

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)

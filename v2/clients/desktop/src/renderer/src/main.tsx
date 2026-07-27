import React from 'react'
import { createRoot } from 'react-dom/client'

// Design fonts — vendored via fontsource (bundled locally; the renderer's strict
// CSP forbids remote fonts, and a local-first app shouldn't need the network to draw).
import '@fontsource/bricolage-grotesque/600.css'
import '@fontsource/bricolage-grotesque/700.css'
import '@fontsource/bricolage-grotesque/800.css'
import '@fontsource/hanken-grotesk/400.css'
import '@fontsource/hanken-grotesk/500.css'
import '@fontsource/hanken-grotesk/600.css'
import '@fontsource/hanken-grotesk/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'

import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import { configureAccounts } from './lib/auth'
import { platform } from './lib/platform'
import { initialTheme } from './state/store'
import './styles.css'

// apply the persisted theme BEFORE first paint (no flash of the wrong theme)
document.documentElement.dataset.theme = initialTheme()

async function boot(): Promise<void> {
  // Hosted flavors declare an accounts URL in distribution.toml ([platform]); it must be
  // known BEFORE first render so the sign-in gate shows on the very first frame instead of
  // flashing the app and then bouncing. Open/BYOK flavors return '' — nothing changes.
  try {
    const flavor = (await platform.flavor()) as { accountsUrl?: string }
    configureAccounts(String(flavor.accountsUrl || ''))
  } catch {
    /* no flavor (or bridge hiccup) => BYOK behavior, same as before */
  }
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  )
}

void boot()

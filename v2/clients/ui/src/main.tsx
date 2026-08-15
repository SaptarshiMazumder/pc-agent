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
import { configureAccounts, restoreSession } from './lib/auth'
import { configurePlatform, discoverPlatform } from './lib/discovery'
import { platform } from './lib/platform'
import { initialTheme } from './state/store'
import './styles.css'

// apply the persisted theme BEFORE first paint (no flash of the wrong theme)
document.documentElement.dataset.theme = initialTheme()

async function boot(): Promise<void> {
  // Where this build's platform lives must be known BEFORE first render, so the sign-in gate
  // shows on the very first frame instead of flashing the app and then bouncing. Open/BYOK
  // flavors return '' for everything — nothing changes for them.
  //
  // TWO SOURCES, ONE PREFERRED. `platformUrl` is the single address a modern build bakes, and
  // everything else (accounts, model proxy, ws, providers) is fetched from it. `accountsUrl` is
  // what older flavors carry and stays as the fallback, so an installer shipped before discovery
  // existed keeps working exactly as it did.
  try {
    const flavor = (await platform.flavor()) as { accountsUrl?: string; platformUrl?: string }
    configurePlatform(String(flavor.platformUrl || ''))
    configureAccounts(String(flavor.accountsUrl || ''))
    // AWAITED, deliberately, despite costing a round trip before the first paint. The document
    // decides whether this build even HAS sign-in, and rendering before the answer arrives means
    // showing the app to someone who then gets bounced to a login screen a moment later. It
    // resolves to null on any failure (offline, unreachable) and the baked values take over, so
    // this can delay startup but cannot prevent it.
    await discoverPlatform()
    // "STAY SIGNED IN", with a ten-minute access token. Nothing durable is kept but the refresh
    // token, so one exchange here turns it into a usable pair before the first frame — otherwise
    // a returning user sees the sign-in gate for a moment and then gets bounced past it.
    // Resolves to null when there is nothing stored or the session is genuinely over.
    await restoreSession()
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

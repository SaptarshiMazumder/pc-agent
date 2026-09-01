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
import { applyStoredTheme, bootPlatform } from './boot'
import ErrorBoundary from './components/ErrorBoundary'
import './styles.css'

applyStoredTheme()

async function boot(): Promise<void> {
  await bootPlatform()
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  )
}

void boot()

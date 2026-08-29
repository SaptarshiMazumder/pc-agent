/**
 * Entry point for admin.html — the standalone admin console.
 *
 * Deliberately the same shape as main.tsx: identical fonts, identical boot, identical error
 * boundary. The ONE difference is what gets rendered, which is the whole point of a second entry —
 * this bundle contains AdminApp and its dependencies, and none of the chat shell.
 */

import React from 'react'
import { createRoot } from 'react-dom/client'

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

import AdminApp from './AdminApp'
import { applyStoredTheme, bootPlatform } from './boot'
import ErrorBoundary from './components/ErrorBoundary'
import './styles.css'

applyStoredTheme()

async function boot(): Promise<void> {
  await bootPlatform()
  createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <AdminApp />
      </ErrorBoundary>
    </React.StrictMode>
  )
}

void boot()

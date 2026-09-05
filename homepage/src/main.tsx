import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Fonts are bundled, not fetched from a CDN — the built site makes no
// third-party requests at all.
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/hanken-grotesk'
import '@fontsource-variable/jetbrains-mono'

import './styles/tokens.css'
import './styles/base.css'
import './styles/sections.css'
import App from './App'

const container = document.getElementById('root')
if (!container) throw new Error('#root is missing from index.html')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

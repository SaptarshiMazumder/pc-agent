import React from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { initialTheme } from './state/store'
import './styles.css'

// apply the persisted theme BEFORE first paint (no flash of the wrong theme)
document.documentElement.dataset.theme = initialTheme()

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)

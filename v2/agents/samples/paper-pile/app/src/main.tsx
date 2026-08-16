import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

// StrictMode double-invokes effects in DEV only. That is a feature here: the subscription in
// useChat has to survive being mounted, cleaned up and mounted again, and if it did not, the
// duplicated-deltas bug would show up on the developer's machine rather than the user's.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

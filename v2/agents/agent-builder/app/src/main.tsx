import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

const host = document.getElementById('root')
// Not a fallback — a hard stop. A missing mount point means index.html and this entry disagree,
// and a page that silently renders nothing is the hardest kind of build error to find.
if (!host) throw new Error('#root is missing from index.html')

createRoot(host).render(<App />)

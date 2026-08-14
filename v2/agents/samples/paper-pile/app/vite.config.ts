import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Served by the daemon out of `agents/<id>/ui/` at `/apps/<id>/`. Three settings follow from
// that, and none of them is a style choice:
//
//   base: './'      assets resolve RELATIVE to the page. An absolute '/assets/…' would ask the
//                   daemon's root instead of this agent's folder, and every chunk 404s.
//   outDir: '../ui' the BUILT output is what ships — `ui/` is packaged into the .agentpkg and
//                   served as-is. `app/` is the source, kept beside it so the next author can
//                   rebuild. Nobody installing this agent ever runs npm.
//   emptyOutDir     otherwise stale hashed chunks from an older build pile up in what we ship.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: '../ui', emptyOutDir: true },
})

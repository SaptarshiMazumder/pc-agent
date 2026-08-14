import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The agent's app is SERVED BY THE DAEMON out of `agents/<id>/ui/`, at `/apps/<id>/`.
// Three settings follow from that and none of them are style choices:
//
//   base: './'      assets must resolve RELATIVE to the page. An absolute '/assets/…' would
//                   request the daemon's root, not this agent's folder, and every chunk 404s.
//   outDir: '../ui' the built output IS what ships. `ui/` is packaged into the .agentpkg and
//                   served as-is; `app/` is the source, kept beside it so the next author can
//                   rebuild. Nobody installing this agent ever runs npm.
//   emptyOutDir     stale hashed chunks from a previous build would otherwise accumulate in the
//                   thing we ship.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../ui',
    emptyOutDir: true,
    // One JS file and one CSS file instead of a module graph. The daemon serves each asset as
    // a separate request with `Cache-Control: no-store`, so splitting buys nothing here and
    // costs a round trip per chunk on every open.
    rollupOptions: { output: { manualChunks: undefined } },
  },
})

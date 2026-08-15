import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Agent Builder's window is SERVED BY THE DAEMON out of `agents/agent-builder/ui/`, at
// `/apps/agent-builder/`. Three settings follow from that and none of them are style choices:
//
//   base: './'      assets must resolve RELATIVE to the page. An absolute '/assets/…' would
//                   request the daemon's root, not this agent's folder, and every chunk 404s.
//   outDir: '../ui' the built output IS what ships and what the daemon serves. `app/` is the
//                   source beside it.
//   emptyOutDir     stale hashed chunks from a previous build would otherwise pile up in the
//                   folder the product serves.
//
// `emptyOutDir` is the reason the scaffolding borrow root had to move out of `ui/`. `md.js` and
// `vendor/agentd-client.js` — the single copies every scaffolded agent is built from — used to
// live in here, and this build would have deleted them. They are now in
// `skills/build-agent/templates/_borrowed/` (see BundleLayout on the Python side). Nothing that
// another part of the product reads may live under this directory.
export default defineConfig({
  plugins: [react()],
  base: './',
  // The markdown renderer is imported from the borrow root rather than copied in here, so the
  // product keeps ONE `md.js` — the same file scaffolded agents get. Dev needs to be told it may
  // read above the app directory; the production build resolves it without this.
  server: { fs: { allow: ['..'] } },
  build: {
    outDir: '../ui',
    emptyOutDir: true,
    // One JS file and one CSS file instead of a module graph. The daemon serves each asset as a
    // separate request with `Cache-Control: no-store`, so splitting buys nothing here and costs
    // a round trip per chunk on every open.
    rollupOptions: { output: { manualChunks: undefined } },
    // The bundle is ~640 KB because the three loading animations (~300 KB of Lottie JSON) and the
    // player are inlined rather than fetched. That is the point: this window is served off
    // localhost, so the bytes are free, and an indicator that 404s while the agent is mid-run
    // would be the worst possible thing to be missing. Vite's 500 KB advisory is aimed at apps
    // shipped over the public internet, which this is not.
    chunkSizeWarningLimit: 900,
  },
})

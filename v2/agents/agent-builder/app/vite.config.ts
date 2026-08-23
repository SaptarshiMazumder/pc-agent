import path from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * Drop the legacy `.woff` copies of every bundled font.
 *
 * @fontsource's stylesheets list `woff2` first and `woff` second, for browsers old enough to
 * lack woff2. This window is served to Chromium, which has supported woff2 since 2016 — so the
 * woff files are ~520 KB of binaries that ship, get committed, and are never once requested.
 *
 * BOTH HALVES MATTER. Deleting the assets alone would leave the CSS pointing at files that are
 * not there: harmless in practice, since the browser takes the woff2 and never asks, but a
 * dangling reference is a thing someone later has to work out. So the `url(...)` is rewritten
 * out of the stylesheet too, and what ships is self-consistent.
 */
function woff2Only(): Plugin {
  return {
    name: 'woff2-only',
    generateBundle(_options, bundle) {
      for (const [name, asset] of Object.entries(bundle)) {
        if (name.endsWith('.woff')) {
          delete bundle[name]
        } else if (name.endsWith('.css') && asset.type === 'asset') {
          asset.source = String(asset.source).replace(/,\s*url\([^)]+\.woff\)\s*format\(['"]woff['"]\)/g, '')
        }
      }
    },
  }
}

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
  plugins: [react(), woff2Only()],
  base: './',
  /* `@agentd/client`, from anywhere.
   *
   * The shared modules under `templates/_common/` live OUTSIDE this app's directory, so node's
   * "walk up looking for node_modules" never reaches the copy installed here. That went unnoticed
   * while the only shared module imported TYPES from the SDK — those erase before the bundler sees
   * them. The credits page imports real values, and without this it fails to resolve.
   *
   * A scaffolded agent has no such problem: `_common/` is copied INTO its src/, beside its own
   * node_modules. This alias exists because Agent Builder alone reads the templates in place. */
  resolve: {
    alias: {
      '@agentd/client': path.resolve(__dirname, 'node_modules/@agentd/client'),
    },
  },
  // The SETTINGS PAGE is imported from `skills/build-agent/templates/_common/`, so the product
  // keeps ONE of it — the same page every scaffolded agent ships. Dev needs to be told it may read
  // above the app directory; the production build resolves it without this.
  // (This used to say `md.js` for the same reason. That borrow is gone: the markdown renderer is
  // react-markdown now, so `_common/settings` is the only thing reaching outside `app/`.)
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

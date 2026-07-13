import { defineConfig } from 'tsup'

export default defineConfig([
  // ESM for bundlers / Node / the desktop client
  {
    entry: { index: 'src/index.ts' },
    format: ['esm'],
    dts: true,
    sourcemap: true,
    clean: true
  },
  // IIFE for no-build agent apps: <script src=".../agentd-client.js"> -> window.agentd
  {
    entry: { 'agentd-client': 'src/index.ts' },
    format: ['iife'],
    globalName: 'agentd',
    sourcemap: false,
    minify: false,
    outExtension: () => ({ js: '.js' })
  }
])

import { defineConfig } from 'tsup'

// `@agentd/auth` and `@agentd/billing` are SOURCE WE OWN, not third-party dependencies, and
// they must land INSIDE both
// bundles. tsup externalises anything in `dependencies` by default, which left the ESM build
// importing a bare '@agentd/auth' specifier — fine here, fatal for every agent: a React agent app
// vendors that file and resolves nothing at that name, so `npm run build` fails for the recipient
// and never for us. The IIFE build inlines it either way; naming it here makes the two agree.
const BUNDLE_IN = ['@agentd/auth', '@agentd/billing']

export default defineConfig([
  // ESM for bundlers / Node / the desktop client
  {
    entry: { index: 'src/index.ts' },
    noExternal: BUNDLE_IN,
    format: ['esm'],
    dts: true,
    sourcemap: true,
    clean: true
  },
  // IIFE for no-build agent apps: <script src=".../agentd-client.js"> -> window.agentd
  {
    entry: { 'agentd-client': 'src/index.ts' },
    noExternal: BUNDLE_IN,
    format: ['iife'],
    globalName: 'agentd',
    sourcemap: false,
    minify: false,
    outExtension: () => ({ js: '.js' })
  }
])

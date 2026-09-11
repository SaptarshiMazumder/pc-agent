/** Build only Figure Creator. Shared canvas/template/SDK sources are strictly read-only. */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const agent = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const v2 = path.resolve(agent, '..', '..')
const canvas = path.join(v2, 'clients', 'canvas')
const require = createRequire(path.join(canvas, 'package.json'))
const { build } = require('esbuild')
const entry = path.join(canvas, 'src', 'index.tsx')
const vendor = path.join(agent, 'ui', 'vendor')

const result = await build({
  entryPoints: [entry],
  bundle: true,
  format: 'iife',
  globalName: 'agentdCanvas',
  outfile: path.join(vendor, 'agentd-canvas.js'),
  write: false,
  minify: true,
  jsx: 'automatic',
  alias: {
    react: path.dirname(require.resolve('react/package.json')),
    'react-dom': path.dirname(require.resolve('react-dom/package.json')),
    '@agentd/client': path.join(agent, 'ui-src', 'page-sdk.ts'),
  },
  // Build-time component composition, NOT source rewriting. Only the shared shell's imports
  // use these product adapters; each adapter can still import the original shared component.
  plugins: [{
    name: 'figure-creator-account-components',
    setup(builder) {
      const components = {
        './account': 'FigureCreatorAccountFooter.tsx',
        './settings': 'FigureCreatorSettingsPage.tsx',
      }
      builder.onResolve({ filter: /^\.\/(account|settings)$/ }, (args) => {
        if (path.resolve(args.importer) !== entry) return
        return { path: path.join(agent, 'ui-src', components[args.path]) }
      })
    },
  }],
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.svg': 'dataurl' },
  logLevel: 'warning',
})

// Validate EVERY generated path before writing any. Never run the shared vendor-all build.
for (const file of result.outputFiles) {
  if (path.dirname(file.path) !== vendor) throw new Error(`Output outside Figure Creator: ${file.path}`)
}
fs.mkdirSync(vendor, { recursive: true })
for (const file of result.outputFiles) fs.writeFileSync(file.path, file.contents)
fs.copyFileSync(path.join(v2, 'clients', 'ui', 'src', 'styles.css'), path.join(vendor, 'agentd-shell.css'))
fs.copyFileSync(path.join(v2, 'clients', 'sdk-js', 'dist', 'agentd-client.js'), path.join(vendor, 'agentd-client.js'))
console.log('Built Figure Creator UI only (shared shell + shared sign-in/credits screens).')

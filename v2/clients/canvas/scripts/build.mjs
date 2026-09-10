/**
 * Build @agentd/canvas → ONE self-contained pair an agent app vendors:
 *
 *   dist/agentd-canvas.js    IIFE, global `agentdCanvas` (React + fabric + the shell's
 *                            canvas components, straight from ../ui/src — no copies)
 *   dist/agentd-canvas.css   the canvas rules EXTRACTED from the shell's styles.css at build
 *                            time (every `.cv-` / `.ws-` block + the theme variables they
 *                            use, with the shell's values as :root fallbacks). Extraction,
 *                            not duplication: restyle the shell and rebuild — apps follow.
 *
 * Then vendors both into every agent that declares a `ui/vendor/` — currently figure-create.
 */

import { build } from 'esbuild'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const pkg = path.resolve(here, '..')
const v2 = path.resolve(pkg, '..', '..')
const require = createRequire(import.meta.url)
// A package install and the clients workspace can each provide the toolchain. Resolve once,
// then pin all imported components to that SAME React, including the shared sign-in card.
const reactDir = path.dirname(require.resolve('react/package.json'))
const reactDomDir = path.dirname(require.resolve('react-dom/package.json'))
const uiSrc = path.join(v2, 'clients', 'ui', 'src')
const dist = path.join(pkg, 'dist')
// Scope a repair to one product: node scripts/build.mjs --agent figure-creator.
// Validate before building or copying anything; misspelled arguments must never vendor all apps.
const TARGETS = [
  { id: 'figure-create', files: ['agentd-canvas.js', 'agentd-canvas.css'] },
  { id: 'figure-creator', files: ['agentd-canvas.js', 'agentd-shell.css'], sdk: true },
]
const args = process.argv.slice(2)
const selected = args.length === 0 ? TARGETS : TARGETS.filter(({ id }) => id === args[1])
if (args.length && (args.length !== 2 || args[0] !== '--agent' || !selected.length)) {
  throw new Error('Expected --agent figure-create or --agent figure-creator')
}
fs.mkdirSync(dist, { recursive: true })

// ---- JS: bundle the entry (which imports the shell's own component sources) --------------
await build({
  entryPoints: [path.join(pkg, 'src', 'index.tsx')],
  bundle: true,
  format: 'iife',
  globalName: 'agentdCanvas',
  outfile: path.join(dist, 'agentd-canvas.js'),
  minify: true,
  jsx: 'automatic',
  // ONE REACT, whatever the disk says. This entry pulls in files from TWO packages — this one and
  // clients/ui — and esbuild resolves `react` relative to the file that imports it, so ui's
  // components bound to clients/node_modules/react while this package's bound to its own. Two
  // instances of React in one bundle is not a size problem, it is a broken one: hooks read a
  // dispatcher off the module singleton, so the second copy's hooks see null and every component
  // using one dies with "Invalid hook call". It surfaced the day the clients workspace gained its
  // own react (the tree that had them hoisted looked fine), which is exactly the kind of thing a
  // build must pin rather than notice.
  alias: {
    react: reactDir,
    'react-dom': reactDomDir,
    '@agentd/client': path.join(pkg, 'src', 'page-client.ts'),
  },
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.svg': 'dataurl' },
  logLevel: 'info',
})

// ---- CSS: extract the canvas rules + the variables they use from the shell ---------------
const css = fs.readFileSync(path.join(uiSrc, 'styles.css'), 'utf-8')
const authCss = fs.readFileSync(path.join(
  v2, 'agents', 'agent-builder', 'skills', 'build-agent', 'templates', '_common', 'auth', 'auth.css'
), 'utf-8')
const blocks = css.match(/[^{}]*\{[^{}]*\}/g) || []
const canvasBlocks = blocks.filter((b) => {
  const sel = b.split('{')[0]
  return sel.includes('.cv-') || sel.includes('.ws-') || sel.includes('.markdown')
})
const used = new Set()
for (const b of canvasBlocks) for (const m of b.matchAll(/var\((--[a-z0-9-]+)/g)) used.add(m[1])
// each var's value from the shell's base :root block (light theme = the app default)
const rootBlock = blocks.find((b) => b.split('{')[0].trim() === ':root') || ''
const varLines = []
for (const name of used) {
  const m = rootBlock.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))
  if (m) varLines.push(`  ${name}: ${m[1].trim()};`)
}
// ---- the WHOLE stylesheet, for `mountShell` ---------------------------------------------
// The narrow sheet above exists for an app that borrows the canvas alone and keeps its own look.
// An app that mounts the full surface is not borrowing a widget, it IS the agentd window — and
// picking selectors for that is a losing game: every shell restyle would silently half-apply.
// Copied rather than curated, so the two can never disagree, and written next to the other
// artifact so vendoring stays one step.
fs.writeFileSync(
  path.join(dist, 'agentd-shell.css'),
  [
    '/* GENERATED by @agentd/canvas build — a verbatim copy of clients/ui/src/styles.css.',
    '   Do not edit: rebuild the package instead (npm run build in v2/clients/canvas). */',
    css,
    authCss,
  ].join('\n')
)

const out = [
  '/* GENERATED by @agentd/canvas build — extracted from clients/ui/src/styles.css. Do not edit. */',
  `:root {\n${varLines.join('\n')}\n}`,
  ...canvasBlocks.map((b) => b.trim()),
  authCss,
].join('\n\n')
fs.writeFileSync(path.join(dist, 'agentd-canvas.css'), out)
console.log(`css: ${canvasBlocks.length} blocks, ${used.size} vars -> agentd-canvas.css`)

// ---- vendor into the agents that use it ---------------------------------------------------
// Each target names WHAT it borrows: the canvas widget alone, or the whole window. Listing the
// files per agent rather than copying all three everywhere keeps a 60KB stylesheet out of an app
// that only wanted a viewer — and makes the difference visible here instead of in a page's <head>.
for (const { id, files, sdk } of selected) {
  const dir = path.join(v2, 'agents', id, 'ui', 'vendor')
  fs.mkdirSync(dir, { recursive: true })
  for (const f of files) {
    fs.copyFileSync(path.join(dist, f), path.join(dir, f))
  }
  // The fallback snapshot must support the same APIs when an older engine cannot substitute
  // its SDK at serve time. No SDK build here: use the canonical artifact already built by CI.
  if (sdk) fs.copyFileSync(
    path.join(v2, 'clients', 'sdk-js', 'dist', 'agentd-client.js'),
    path.join(dir, 'agentd-client.js'),
  )
  console.log(`vendored -> ${path.relative(v2, dir)} (${files.join(', ')})`)
}

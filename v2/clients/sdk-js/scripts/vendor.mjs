/**
 * vendor — push the freshly built IIFE SDK into every place an agent app loads it from.
 *
 *   node scripts/vendor.mjs            (runs automatically after `npm run build`)
 *
 * WHY THIS EXISTS. `UiTemplate.borrowed` says vendor/agentd-client.js is copied from one shared
 * source so that "one source, copied at scaffold time, cannot drift". True for the copy INTO a
 * new agent — but nothing was copying the built SDK into that source in the first place, so the
 * one source was itself a hand-updated file. It had already fallen behind dist/ by 12 KB.
 *
 * The drift is quiet and nasty: an agent app keeps whatever SDK it was scaffolded with, so a
 * method added to the SDK is simply absent on window.agentd — `agentd.mountSignInGate is not a
 * function` in one agent and fine in the next, with no version anywhere to compare.
 *
 * TARGETS: the borrow root under agent-builder's templates (the canonical copy new agents are
 * scaffolded from) plus every agents/<id>/ui/vendor/agentd-client.js that ALREADY exists.
 * Existing apps are updated because a stale SDK talking to a current daemon is the failure this
 * prevents; an agent with no vendor/ dir is not given one, since that is a UI-less agent, not an
 * out-of-date one.
 *
 * The canonical copy was `agent-builder/ui/vendor/` until that folder became a Vite build output.
 * Writing the one source of the SDK into a directory that `npm run build` empties is a race the
 * scaffolder loses, so it moved next to the templates that borrow it.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const sdkDir = path.resolve(here, '..')
const v2 = path.resolve(sdkDir, '..', '..')

const built = path.join(sdkDir, 'dist', 'agentd-client.js')
if (!fs.existsSync(built)) {
  console.error(`vendor: no build at ${built} — run tsup first`)
  process.exit(1)
}
const bytes = fs.readFileSync(built)

const targets = []
// Kept in step with BundleLayout.BORROW_ROOT on the Python side. If these two disagree, scaffolding
// hands new agents whatever stale SDK happens to be at the path it reads.
const canonical = path.join(
  v2, 'agents', 'agent-builder', 'skills', 'build-agent', 'templates', '_borrowed',
  'vendor', 'agentd-client.js'
)
targets.push(canonical) // written even if absent: this is the copy scaffolding reads

const agentsDir = path.join(v2, 'agents')
if (fs.existsSync(agentsDir)) {
  for (const entry of fs.readdirSync(agentsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const candidate = path.join(agentsDir, entry.name, 'ui', 'vendor', 'agentd-client.js')
    if (candidate !== canonical && fs.existsSync(candidate)) targets.push(candidate)
  }
}

// THE OTHER SHAPE OF APP. The IIFE above is for a plain ui/ that loads the SDK with a <script>
// tag. A BUILT app (React/Vite) imports `@agentd/client` instead, and resolves it from
// package.json — which in this repo is a relative `file:` path into clients/sdk-js. That path
// exists only here: an agent scaffolded into the user's own agents dir has nothing at
// ../../../../clients/sdk-js, so `npm install` fails and the app never builds at all.
//
// So the React starter carries the ESM bundle and its types INSIDE itself, aliased in
// vite.config.ts, with no dependency on this repo or on a published package. Vendored from the
// same build as the IIFE, in the same run, so the two can never disagree about what the SDK is.
const reactVendor = path.join(
  v2, 'agents', 'agent-builder', 'skills', 'build-agent', 'templates', '_borrowed',
  'react', 'vendor'
)
const pairs = [
  [built, targets[0]],
  [path.join(sdkDir, 'dist', 'index.js'), path.join(reactVendor, 'agentd-client.js')],
  [path.join(sdkDir, 'dist', 'index.d.ts'), path.join(reactVendor, 'agentd-client.d.ts')],
  ...targets.slice(1).map((t) => [built, t]),
]

let updated = 0
let total = 0
for (const [source, target] of pairs) {
  if (!fs.existsSync(source)) {
    console.error(`vendor: no build at ${source} — run tsup first`)
    process.exit(1)
  }
  const payload = fs.readFileSync(source)
  total += payload.length
  const before = fs.existsSync(target) ? fs.readFileSync(target) : null
  if (before && before.equals(payload)) continue // byte-identical: leave the mtime alone
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, payload)
  updated++
  console.log(`  vendored -> ${path.relative(v2, target).replace(/\\/g, '/')}`)
}
console.log(
  `vendor: ${updated} updated, ${pairs.length - updated} already current ` +
    `(${(total / 1024).toFixed(1)} KB across ${pairs.length} file(s))`
)

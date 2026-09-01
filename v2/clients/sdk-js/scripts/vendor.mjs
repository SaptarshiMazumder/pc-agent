/**
 * vendor — push the freshly built SDK into every place a React agent app imports it from.
 *
 *   node scripts/vendor.mjs            (runs automatically after `npm run build`)
 *
 * WHY THIS EXISTS. `UiTemplate.borrowed` says vendor/agentd-client.js is copied from one shared
 * source so that "one source, copied at scaffold time, cannot drift". True for the copy INTO a
 * new agent — but nothing was copying the built SDK into that source in the first place, so the
 * one source was itself a hand-updated file. It had already fallen behind dist/ by 12 KB.
 *
 * The drift is quiet and nasty: an agent app keeps whatever SDK it was scaffolded with, so a
 * method added to the SDK is simply absent on window.agentd — `agentd.somethingNew is not a
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

/** Are these the same file, ignoring how the platform ends its lines?
 *
 *  Compared as normalised TEXT rather than bytes. The alternative — writing CRLF on Windows to
 *  match what git checks out — would make the vendored copy differ from the build it came from,
 *  and this script exists precisely so those cannot differ.
 */
function sameContent(a, b) {
  return a.toString('utf8').replace(/\r\n/g, '\n') === b.toString('utf8').replace(/\r\n/g, '\n')
}

const here = path.dirname(fileURLToPath(import.meta.url))
const sdkDir = path.resolve(here, '..')
const v2 = path.resolve(sdkDir, '..', '..')

// The ESM bundle is what every target takes now; the guard names it so a missing build is
// reported as a missing build rather than as an unreadable copy further down.
const built = path.join(sdkDir, 'dist', 'index.js')
if (!fs.existsSync(built)) {
  console.error(`vendor: no build at ${built} — run tsup first`)
  process.exit(1)
}
const bytes = fs.readFileSync(built)

// Kept in step with BundleLayout.BORROW_ROOT on the Python side. If these two disagree, scaffolding
// hands new agents whatever stale SDK happens to be at the path it reads.
// NOTHING VENDORS THE IIFE ANY MORE. It was for hand-written vanilla `ui/` folders that loaded
// the SDK with a <script> tag; those templates are deleted and nothing scaffolds one. A dozen
// older agents still have such a folder and keep whatever SDK they shipped with — re-vendoring
// them meant every build rewrote ten files nobody had asked to change.

const agentsDir = path.join(v2, 'agents')

// EVERY ROOT THAT HOLDS AN AGENT, not just `agents/*`. Samples live one level deeper, under
// `agents/samples/<id>/`, and they are real agents — the registry scans them, the loader loads
// their plugins, and their apps ship a vendored SDK like any other. Scanning one level meant a
// sample's copy was never refreshed and silently drifted from the daemon it talks to, which is
// the exact failure vendoring exists to prevent.
function agentDirs(root) {
  if (!fs.existsSync(root)) return []
  const dirs = []
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const dir = path.join(root, entry.name)
    dirs.push(dir)
    if (entry.name === 'samples') {
      for (const sub of fs.readdirSync(dir, { withFileTypes: true })) {
        if (sub.isDirectory()) dirs.push(path.join(dir, sub.name))
      }
    }
  }
  return dirs
}

// REACT APPS ONLY — `app/vendor/`, the ESM bundle a bundler imports.
//
// The plain `ui/vendor/` IIFE is NOT refreshed any more. A dozen older agents still carry one and
// are served straight off disk; they keep working on whatever SDK they shipped with, and nothing
// maintains them. Re-vendoring them meant every `npm run build` rewrote ten files nobody had
// asked to change, which turns an unrelated diff into ten, and none of those agents is going to
// be rebuilt in vanilla anyway.
//
// What IS kept current: the borrow root that new agents are scaffolded from, and the React apps
// under `agents/` — agent-builder and the samples, which are the blueprints.
const esmVendorDirs = []
for (const dir of agentDirs(agentsDir)) {
  const esm = path.join(dir, 'app', 'vendor', 'agentd-client.js')
  if (fs.existsSync(esm)) esmVendorDirs.push(path.join(dir, 'app', 'vendor'))
}
// ACCOUNT OVERLAYS TOO. A signed-in author's agents live under .agentd/accounts/<acct>/agents/,
// each carrying its own vendored SDK — exactly the copies-that-get-missed this script exists to
// prevent, and the ones missed first, because they are the agents somebody is actively building.
const accountsRoot = path.join(v2, '.agentd', 'accounts')
if (fs.existsSync(accountsRoot)) {
  for (const acct of fs.readdirSync(accountsRoot)) {
    const overlay = path.join(accountsRoot, acct, 'agents')
    if (!fs.existsSync(overlay)) continue
    for (const dir of agentDirs(overlay)) {
      const esm = path.join(dir, 'app', 'vendor', 'agentd-client.js')
      if (fs.existsSync(esm)) esmVendorDirs.push(path.join(dir, 'app', 'vendor'))
    }
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
// `_skeleton/` — the complete window every new agent starts as. This pointed at
// `_borrowed/react` after the skeleton replaced it, so a rebuild refreshed a directory nothing
// scaffolds from (recreating it after it was deleted) while the copy every agent actually
// receives went stale.
const reactVendor = path.join(
  v2, 'agents', 'agent-builder', 'skills', 'build-agent', 'templates', '_skeleton', 'vendor'
)
const pairs = [
  [path.join(sdkDir, 'dist', 'index.js'), path.join(reactVendor, 'agentd-client.js')],
  [path.join(sdkDir, 'dist', 'index.d.ts'), path.join(reactVendor, 'agentd-client.d.ts')],
  // Built apps that already vendor the SDK — refreshed from the SAME dist as the starter, so a
  // sample and the template it teaches from can never disagree about what the SDK is.
  ...esmVendorDirs.flatMap((dir) => [
    [path.join(sdkDir, 'dist', 'index.js'), path.join(dir, 'agentd-client.js')],
    [path.join(sdkDir, 'dist', 'index.d.ts'), path.join(dir, 'agentd-client.d.ts')],
  ]),
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
  // SAME CONTENT, DIFFERENT LINE ENDINGS IS NOT A CHANGE.
  //
  // This build writes LF. On Windows `core.autocrlf=true` checks these files out as CRLF, so a
  // byte comparison NEVER matched and every run rewrote all seventeen — reporting "17 updated"
  // while changing nothing anybody wrote. The cost was not the writes: it was seventeen files
  // in every changeset with an empty diff, which is how a real change gets missed.
  if (before && sameContent(before, payload)) continue
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, payload)
  updated++
  console.log(`  vendored -> ${path.relative(v2, target).replace(/\\/g, '/')}`)
}
console.log(
  `vendor: ${updated} updated, ${pairs.length - updated} already current ` +
    `(${(total / 1024).toFixed(1)} KB across ${pairs.length} file(s))`
)

// PREBUILT TEMPLATES RIDE THE SAME TRAIN. They embed the SDK just vendored above, and
// create_agent copies them in as a new agent's ui/ — stale here is a stale product, not a
// stale thumbnail (the thumbnails are _previews/, a separate Gate-less display flavor). The script hashes its inputs and answers "current" in
// under a second when nothing changed, so the inner build loop stays fast; when it DOES
// build and fails, this vendor run fails with it, because shipping without previews would
// re-open the gap this pipeline exists to close.
{
  const { spawnSync } = await import('node:child_process')
  const script = path.join(
    v2, 'agents', 'agent-builder', 'skills', 'build-agent', 'templates',
    'build_prebuilt_templates.py'
  )
  const py = process.platform === 'win32' ? 'python' : 'python3'
  const r = spawnSync(py, [script], { stdio: 'inherit' })
  if (r.status !== 0) {
    console.error('vendor: prebuilt templates failed to build (see above)')
    process.exit(1)
  }
}

/**
 * vendor — push the freshly built IIFE SDK into every place an agent app loads it from.
 *
 *   node scripts/vendor.mjs            (runs automatically after `npm run build`)
 *
 * WHY THIS EXISTS. `UiTemplate.borrowed` says vendor/agentd-client.js comes from Agent Builder's
 * live ui/ so that "one source, copied at scaffold time, cannot drift". True for the copy INTO a
 * new agent — but nothing was copying the built SDK into that live ui/ in the first place, so the
 * one source was itself a hand-updated file. It had already fallen behind dist/ by 12 KB.
 *
 * The drift is quiet and nasty: an agent app keeps whatever SDK it was scaffolded with, so a
 * method added to the SDK is simply absent on window.agentd — `agentd.mountSignInGate is not a
 * function` in one agent and fine in the next, with no version anywhere to compare.
 *
 * TARGETS: Agent Builder's ui/vendor (the canonical copy new agents are scaffolded from) plus
 * every agents/<id>/ui/vendor/agentd-client.js that ALREADY exists. Existing apps are updated
 * because a stale SDK talking to a current daemon is the failure this prevents; an agent with no
 * vendor/ dir is not given one, since that is a UI-less agent, not an out-of-date one.
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
const canonical = path.join(v2, 'agents', 'agent-builder', 'ui', 'vendor', 'agentd-client.js')
targets.push(canonical) // written even if absent: this is the copy scaffolding reads

const agentsDir = path.join(v2, 'agents')
if (fs.existsSync(agentsDir)) {
  for (const entry of fs.readdirSync(agentsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const candidate = path.join(agentsDir, entry.name, 'ui', 'vendor', 'agentd-client.js')
    if (candidate !== canonical && fs.existsSync(candidate)) targets.push(candidate)
  }
}

let updated = 0
for (const target of targets) {
  const before = fs.existsSync(target) ? fs.readFileSync(target) : null
  if (before && before.equals(bytes)) continue // byte-identical: leave the mtime alone
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, bytes)
  updated++
  console.log(`  vendored -> ${path.relative(v2, target).replace(/\\/g, '/')}`)
}
console.log(
  `vendor: ${updated} updated, ${targets.length - updated} already current ` +
    `(${(bytes.length / 1024).toFixed(1)} KB)`
)

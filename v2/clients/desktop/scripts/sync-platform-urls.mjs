/**
 * sync-platform-urls — point the build flavors at the CURRENT infrastructure.
 *
 *   npm run sync:urls                 # strict: fail loudly if terraform can't answer
 *   npm run sync:urls -- --env prod   # a different environment (default: dev)
 *   npm run sync:urls -- --check      # report drift, write nothing (CI guard)
 *
 * Why this exists: a flavor's [platform]/[store] URLs are terraform OUTPUTS that were
 * copied into the file by hand. An ALB's DNS name carries an AWS-assigned suffix, so ANY
 * destroy/recreate mints a new hostname and every hand-copied URL silently rots — cloud
 * mode then dies with `Litellm_proxyException - Connection error` (a DNS failure, three
 * layers down). Terraform already knows the live values; this reads them back.
 *
 * Runs as `predev`, softly: no terraform / no state / no creds => warn and continue, so a
 * plain `npm run dev` never depends on the infra toolchain. `--strict` (what sync:urls
 * uses) turns those skips into failures.
 *
 * DERIVATION, NOT AUTHORING. It rewrites ONLY keys a flavor already declares — a BYOK
 * flavor with no [platform] section never grows one. Which outputs map to which keys is
 * the table below; which flavors exist is discovered from disk. Nothing here is a URL.
 *
 * Two exemptions, both read off the flavor itself rather than a list kept here:
 *   • a LOOPBACK value (flavors/hosted-dev runs the whole platform on 127.0.0.1) — the
 *     value states the intent: this flavor is wired to a local stack, not to a cloud one
 *   • a `sync-urls: skip` comment anywhere in the file — the explicit escape hatch (e.g.
 *     a flavor pinned to a frozen environment)
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(desktopDir, '..', '..', '..') // pc-agent/

// terraform output name -> the distribution.toml key it fills. Add a row to sync a new one.
const SYNCED = {
  accounts_url: 'accounts_url',
  model_gateway_url: 'model_gateway_url',
  registry_url: 'registry_url'
}

// ---- args ---------------------------------------------------------------------------
const argv = process.argv.slice(2)
const has = (flag) => argv.includes(`--${flag}`)
const opt = (name) => {
  const i = argv.indexOf(`--${name}`)
  return i >= 0 ? (argv[i + 1] || '').trim() : ''
}
const strict = has('strict')
const checkOnly = has('check')
const env = opt('env') || (process.env.AGENTD_INFRA_ENV || '').trim() || 'dev'
const envDir = path.join(repoRoot, 'v2', 'infra', 'environments', env)

const log = (msg) => console.log(`[sync-urls] ${msg}`)
/** Soft by default (dev must not need terraform); --strict makes any skip fatal. */
function skip(msg) {
  if (strict) {
    console.error(`[sync-urls] ${msg}`)
    process.exit(1)
  }
  log(`${msg} — leaving flavors as they are`)
  process.exit(0)
}

// ---- read the live values from terraform ---------------------------------------------
// Local state => this is a file read, not an AWS call (fast, works offline).
if (!fs.existsSync(envDir)) skip(`no infra environment at ${path.relative(repoRoot, envDir)}`)

let outputs
try {
  const raw = execFileSync('terraform', [`-chdir=${envDir}`, 'output', '-json'], {
    encoding: 'utf-8',
    timeout: 60_000,
    stdio: ['ignore', 'pipe', 'pipe']
  })
  outputs = JSON.parse(raw)
} catch (error) {
  const why = error?.code === 'ENOENT' ? 'terraform not on PATH' : String(error?.stderr || error?.message || error).trim()
  skip(`could not read '${env}' outputs (${why})`)
}

// output -> value, keeping only the ones we sync that actually have a value
const wanted = {}
for (const [output, key] of Object.entries(SYNCED)) {
  const value = String(outputs?.[output]?.value ?? '').trim()
  if (value) wanted[key] = value
}
if (!Object.keys(wanted).length) skip(`'${env}' has none of: ${Object.keys(SYNCED).join(', ')}`)

// ---- find every flavor on disk (authored + generated) --------------------------------
function flavorFiles() {
  const roots = [path.join(desktopDir, 'flavors'), path.join(desktopDir, 'dist', 'app-flavors')]
  const files = []
  for (const root of roots) {
    let entries = []
    try {
      entries = fs.readdirSync(root, { withFileTypes: true })
    } catch {
      continue // dist/app-flavors only exists after gen:app
    }
    for (const entry of entries) {
      const file = path.join(root, entry.name, 'distribution.toml')
      if (entry.isDirectory() && fs.existsSync(file)) files.push(file)
    }
  }
  return files
}

// ---- rewrite in place, preserving comments -------------------------------------------
// Surgical line edit rather than TOML parse+stringify: these files are mostly prose
// (why HTTP is temporary, what each block means) and a round-trip would drop all of it.
// `^\s*key\s*=` only matches a real assignment — a '#' comment mentioning the key can't.
function retarget(text, key, value) {
  const line = new RegExp(`^([ \\t]*${key}[ \\t]*=[ \\t]*)(.*)$`, 'm')
  const match = text.match(line)
  if (!match) return text // this flavor doesn't declare the key — never add it
  if (LOOPBACK.test(match[2])) return text // a local-stack flavor; its value IS the intent
  return text.replace(line, `$1${JSON.stringify(value)}`)
}

/** A value aimed at this machine: localhost in any of its spellings. */
const LOOPBACK = /["']?(https?:\/\/)?(127(\.\d+){3}|localhost|\[::1\]|0\.0\.0\.0)([:/]|["']|$)/i

const OPT_OUT = /sync-urls:\s*skip/i

let changed = 0
let scanned = 0
for (const file of flavorFiles()) {
  const before = fs.readFileSync(file, 'utf-8')
  scanned++
  if (OPT_OUT.test(before)) continue
  let after = before
  for (const [key, value] of Object.entries(wanted)) after = retarget(after, key, value)
  if (after === before) continue
  changed++
  const rel = path.relative(desktopDir, file).replace(/\\/g, '/')
  if (checkOnly) {
    log(`STALE: ${rel}`)
    continue
  }
  fs.writeFileSync(file, after)
  log(`updated ${rel}`)
}

if (checkOnly && changed) {
  console.error(`[sync-urls] ${changed} flavor(s) drifted from '${env}' — run: npm run sync:urls`)
  process.exit(1)
}
log(
  changed
    ? `${changed}/${scanned} flavor(s) retargeted at '${env}' (${Object.values(wanted).join(', ')})`
    : `${scanned} flavor(s) already match '${env}'`
)

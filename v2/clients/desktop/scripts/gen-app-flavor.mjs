/**
 * gen-app-flavor — turn ANY app agent into a buildable per-agent product flavor.
 *
 *   npm run gen:app -- <agent-id> [--name "Display Name"] [--agents-dir <path>] [--pkg <file>]
 *
 * Produces dist/app-flavors/<id>/ (BUILD OUTPUT — gitignored, regenerated every run,
 * never authored; flavors/ stays reserved for hand-written product flavors):
 *   distribution.toml      [product] app_agent = <id>  → the shell boots straight into
 *                          the agent's own UI (no JARVIS renderer)
 *   bundles/<id>-*.agentpkg  packed from the agent dir (or copied from --pkg)
 *   electron-builder.yml   per-agent build config
 *   icon.ico               extracted from the AGENT's folder ([app] icon / icon.ico)
 *
 * Then: npm run dist:app -- <agent-id>   → "<Name> Setup <ver>.exe"
 *
 * ONE generic pipeline — per-agent products are build parameters, never code forks.
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import TOML from '@iarna/toml'

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(desktopDir, '..', '..', '..') // pc-agent/

function fail(msg) {
  console.error(`gen-app-flavor: ${msg}`)
  process.exit(1)
}

// ---- args ------------------------------------------------------------------------------
const argv = process.argv.slice(2)
const agentId = (argv.find((a) => !a.startsWith('--')) || '').trim()
if (!agentId) fail('usage: npm run gen:app -- <agent-id> [--name X] [--agents-dir D] [--pkg F]')
const opt = (name) => {
  const i = argv.indexOf(`--${name}`)
  return i >= 0 ? argv[i + 1] : ''
}
const agentsDir = path.resolve(desktopDir, opt('agents-dir') || path.join(repoRoot, 'v2', 'agents'))
const agentDir = path.join(agentsDir, agentId)

// ---- read the agent's own declaration (name / version / [app] / icon) -------------------
// EVERYTHING product-related is authored INSIDE agents/<id>/ — the flavor generated below
// is pure derivation, never a thing an author writes. With --pkg and no local agent dir
// (third-party intake: only the published .agentpkg is on this machine), metadata comes
// from flags instead (--name required).
const prebuilt = opt('pkg')
let toml = { app: {} }
const tomlPath = path.join(agentDir, 'agent.toml')
if (fs.existsSync(tomlPath)) {
  toml = TOML.parse(fs.readFileSync(tomlPath, 'utf-8'))
  if (!toml.app) fail(`'${agentId}' has no [app] section — only app agents can become products`)
} else if (!prebuilt) {
  fail(`no agent at ${agentDir} (or pass --pkg <file> for a published .agentpkg)`)
} else if (!opt('name')) {
  fail(`--pkg without a local agent dir needs --name "Display Name"`)
}
const name = opt('name') || String(toml.app.title || toml.name || agentId)
const version = String(toml.version || '1.0.0')

// generated output lives under dist/ — NOT flavors/ (that dir is for authored flavors only)
const flavorRel = `dist/app-flavors/${agentId}`
const flavorDir = path.join(desktopDir, 'dist', 'app-flavors', agentId)
const bundlesDir = path.join(flavorDir, 'bundles')
fs.rmSync(flavorDir, { recursive: true, force: true }) // fully derived — regenerate clean
fs.mkdirSync(bundlesDir, { recursive: true })

// ---- the .agentpkg: copy a prebuilt one, or pack via the agentd CLI ---------------------
if (prebuilt) {
  fs.copyFileSync(path.resolve(prebuilt), path.join(bundlesDir, path.basename(prebuilt)))
} else {
  const exe = process.platform === 'win32' ? 'agentd.exe' : 'agentd'
  const venvCli = path.join(repoRoot, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin', exe)
  const cli = fs.existsSync(venvCli) ? venvCli : 'agentd' // repo venv first, else PATH
  execFileSync(cli, ['bundle', 'pack', agentDir, '--out', bundlesDir], { stdio: 'inherit' })
}

// ---- icon: ships WITH the agent, like everything else it owns ---------------------------
// [app] icon = "<path relative to the agent dir>" wins; else <agent>/icon.ico; else the
// --icon flag (pkg-only intake); else the shell's default icon.
let iconSrc = ''
const declaredIcon = String(toml.app?.icon || '')
if (declaredIcon && fs.existsSync(path.join(agentDir, declaredIcon))) {
  iconSrc = path.join(agentDir, declaredIcon)
} else if (fs.existsSync(path.join(agentDir, 'icon.ico'))) {
  iconSrc = path.join(agentDir, 'icon.ico')
} else if (opt('icon') && fs.existsSync(path.resolve(opt('icon')))) {
  iconSrc = path.resolve(opt('icon'))
}
if (iconSrc) fs.copyFileSync(iconSrc, path.join(flavorDir, 'icon.ico'))

// ---- hosted platform: inherit [platform] from the CORE flavor (one source of truth) --------
// An app-agent product is a full desktop client: if the core build is a HOSTED product (its
// distribution.toml declares [platform] accounts_url/model_gateway_url), the app inherits the
// SAME backend, so its exe signs in and runs on our keys too. No core [platform] => a BYOK
// app-agent (local keys), unchanged. Overridable with --accounts-url / --model-gateway-url.
let platformBlock = ''
const coreDistPath = path.join(desktopDir, 'flavors', 'core', 'distribution.toml')
let corePlatform = {}
try {
  corePlatform = (TOML.parse(fs.readFileSync(coreDistPath, 'utf-8')).platform) || {}
} catch {
  /* no core flavor / unreadable — app-agent stays BYOK */
}
const accountsUrl = opt('accounts-url') || String(corePlatform.accounts_url || '')
const gatewayUrl = opt('model-gateway-url') || String(corePlatform.model_gateway_url || '')
if (accountsUrl && gatewayUrl) {
  platformBlock = `
[platform]
accounts_url = ${JSON.stringify(accountsUrl)}
model_gateway_url = ${JSON.stringify(gatewayUrl)}
`
  console.log(`  hosted: sign-in ${accountsUrl}, gateway ${gatewayUrl}`)
}

// ---- distribution.toml: the ONE knob that makes the shell this agent's product ----------
const dist = `# ${name} — generated app-shell flavor (gen-app-flavor.mjs). [product] app_agent
# switches the shared shell into AGENT-APP mode: daemon find-or-start, first-run bundle
# install, then the agent's own ui/ as the product window. No JARVIS renderer.

[product]
id = ${JSON.stringify(`${agentId}-app`)}
name = ${JSON.stringify(name)}
app_agent = ${JSON.stringify(agentId)}
default_agent = ${JSON.stringify(agentId)}
preinstalled_bundles = [${JSON.stringify(agentId)}]

[store]
enabled = false
${platformBlock}`
fs.writeFileSync(path.join(flavorDir, 'distribution.toml'), dist)

// ---- electron-builder config (per-agent file > env-macro tricks: deterministic) ---------
const icon = fs.existsSync(path.join(flavorDir, 'icon.ico'))
  ? `${flavorRel}/icon.ico`
  : 'resources/icon.ico'
const eb = `# ${name} installer — generated by gen-app-flavor.mjs. Same shell code as every
# product; only these resources differ. Build: npm run dist:app -- ${agentId}
appId: dev.agentd.app.${agentId}
productName: ${name}
directories:
  output: dist/app/${agentId}
files:
  - out/**
extraResources:
  - from: ${flavorRel}/distribution.toml
    to: distribution.toml
  - from: ${flavorRel}/bundles
    to: bundles
    filter: ['*.agentpkg']
  - from: runtime/cpython
    to: python
    filter: ['**/*']
win:
  target: nsis
  icon: ${icon}
nsis:
  oneClick: false
  perMachine: false # per-user install: runtime pip-plugins can write, no admin needed
  allowToChangeInstallationDirectory: true
`
fs.writeFileSync(path.join(flavorDir, 'electron-builder.yml'), eb)

console.log(`\nflavor generated: ${flavorRel}/  (product: "${name}" v${version}; build output, gitignored)`)
console.log(`  dev run:   npx cross-env AGENTD_FLAVOR=${agentId} electron-vite dev`)
console.log(`  installer: npm run dist:app -- ${agentId}`)

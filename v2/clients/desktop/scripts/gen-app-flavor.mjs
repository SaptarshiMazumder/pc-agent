/**
 * gen-app-flavor — turn ANY app agent into a buildable per-agent product flavor.
 *
 *   npm run gen:app -- <agent-id> [--name X] [--version V] [--agents-dir D] [--pkg F]
 *
 * A THIN CALLER. It used to decide what a product IS — display-name precedence, version
 * precedence, icon precedence, the appId, whether [platform] is inherited — in JavaScript, and
 * hand-assemble distribution.toml by string concatenation. That is now ONE implementation in
 * `agent_runtime/domain/product.py`, reached through:
 *
 *     agentd product payload <id> --out <flavor dir>
 *
 * Why it had to move rather than be tidied: the same rules have to run where there is no node —
 * on the publish service, which builds an installer from an uploaded .agentpkg. Two copies of
 * "what version is this product?" is not a hypothetical; one agent shipped three different
 * versions of itself at once (agent.toml 1.1.0, the registry 1.0.0, the exe 0.1.5), and because
 * installs supersede BY VERSION the effect was authors publishing updates nobody received.
 *
 * WHAT IS STILL THIS SCRIPT'S JOB: the electron-builder config. That is knowledge about THIS
 * repo's layout (where runtime/cpython sits, which files electron packs), so it belongs to a
 * repo-local build script and not to the runtime.
 *
 * Produces dist/app-flavors/<id>/ (BUILD OUTPUT — gitignored, regenerated every run):
 *   payload/distribution.toml        written by `agentd product payload`
 *   payload/bundles/<id>-*.agentpkg  same
 *   payload/icon.ico                 same (normalised name, whatever the agent called it)
 *   electron-builder.yml             written here
 *
 * The payload lives in its own subdirectory because that is what an ENGINE consumes with
 * `--app-dir`: one directory, exactly the files a product needs, nothing of the build around it.
 * The whole flavour dir is WIPED first — when this script wrote distribution.toml at the root and
 * then started writing it under payload/, the stale root copy survived and electron-builder went
 * on packaging it. A build that ships a file the generator no longer writes has no visible symptom.
 *
 * Then: npm run dist:app -- <agent-id>   → "<Name> Setup <ver>.exe"
 *
 * NOTE ON THE TWO SHAPES. This path builds a FULL standalone per-agent installer (~250 MB: the
 * client plus the embedded runtime). The engine+stub path — `agentd product build` — produces a
 * ~200 KB installer that shares one engine, and is what the marketplace serves. This one remains
 * for building the engine's own flavors and for a genuinely self-contained one-off.
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import TOML from '@iarna/toml'

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(desktopDir, '..', '..', '..') // pc-agent/
const v2Dir = path.join(repoRoot, 'v2')

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
const agentsDir = path.resolve(desktopDir, opt('agents-dir') || path.join(v2Dir, 'agents'))

// generated output lives under dist/ — NOT flavors/ (that dir is for authored flavors only)
const flavorRel = `dist/app-flavors/${agentId}`
const payloadRel = `${flavorRel}/payload`
const flavorDir = path.join(desktopDir, 'dist', 'app-flavors', agentId)
const payloadDir = path.join(flavorDir, 'payload')
// Fully derived: wipe it. See the header — a leftover from an earlier layout is invisible and
// gets packaged.
fs.rmSync(flavorDir, { recursive: true, force: true })

// ---- the agentd CLI: the one implementation of everything above --------------------------
// Resolution order, first hit wins:
//   1. the repo venv's console script          (.venv/Scripts/agentd.exe)
//   2. that venv's python -m agent_runtime.cli.main — same interpreter, no console script
//      needed. It must be agent_runtime.cli.main and NOT `-m agent_runtime`: the latter is the
//      DAEMON entry point, which ignores the subcommand and tries to bind the gateway port.
//   3. `agentd` on PATH                        (activated venv / global install)
// The venv is DISCOVERED, not assumed: it has lived at both v2/.venv and the repo root, and
// hardcoding one made this die on a working checkout.
function resolveCli() {
  const win = process.platform === 'win32'
  for (const root of [v2Dir, repoRoot]) {
    const bin = path.join(root, '.venv', win ? 'Scripts' : 'bin')
    const cli = path.join(bin, win ? 'agentd.exe' : 'agentd')
    if (fs.existsSync(cli)) return { cli, prefix: [] }
    const python = path.join(bin, win ? 'python.exe' : 'python')
    if (fs.existsSync(python)) return { cli: python, prefix: ['-m', 'agent_runtime.cli.main'] }
  }
  return { cli: 'agentd', prefix: [] }
}

const { cli, prefix } = resolveCli()
const args = [...prefix, 'product', 'payload', '--out', flavorDir, '--agents-dir', agentsDir]
if (opt('pkg')) args.push('--pkg', path.resolve(opt('pkg')))
else args.push(agentId)
for (const flag of ['name', 'version', 'icon']) {
  if (opt(flag)) args.push(`--${flag}`, opt(flag))
}

// ---- hosted platform: the CORE flavor is where THIS REPO says which backend to use ---------
// An app-agent product is a full desktop client: if the core build is HOSTED (its
// distribution.toml declares [platform] accounts_url/model_proxy_url), the app inherits the SAME
// backend, so its exe signs in and runs on our keys too. No core [platform] => BYOK, unchanged.
//
// Read HERE and passed as flags, rather than by the runtime: a repo-relative path is exactly the
// thing that does not exist on a publish service. The runtime's own default is this install's
// distribution profile; this script overrides it because a checkout's profile is the OPEN one.
let corePlatform = {}
try {
  const coreDist = path.join(desktopDir, 'flavors', 'core', 'distribution.toml')
  corePlatform = TOML.parse(fs.readFileSync(coreDist, 'utf-8')).platform || {}
} catch {
  /* no core flavor / unreadable — app-agent stays BYOK */
}
const accountsUrl = opt('accounts-url') || String(corePlatform.accounts_url || '')
// THE ONE ADDRESS the product discovers everything else from. Inherited like the two below, so a
// creator's exe built from a hosted checkout resolves sign-in and the model proxy at RUNTIME
// instead of baking whichever load balancer happened to exist on build day.
const platformUrl = opt('platform-url') || String(corePlatform.platform_url || '')
const proxyUrl =
  opt('model-proxy-url') ||
  opt('model-gateway-url') ||
  String(corePlatform.model_proxy_url || corePlatform.model_gateway_url || '')
if (accountsUrl && proxyUrl) {
  args.push('--accounts-url', accountsUrl, '--model-proxy-url', proxyUrl)
  if (platformUrl) args.push('--platform-url', platformUrl)
  console.log(`  hosted: platform ${platformUrl || '(none - falls back to baked urls)'}`)
  console.log(`          sign-in ${accountsUrl}, model proxy ${proxyUrl}`)
}

// Run from v2/: when agentd is NOT pip-installed in the venv (a plain checkout — the package
// resolves only because cwd is on sys.path), the -m form fails from anywhere else.
execFileSync(cli, args, { stdio: 'inherit', cwd: v2Dir })

// ---- read back what the runtime decided --------------------------------------------------
// The product's identity is now WHATEVER IS IN THE PAYLOAD. Reading it back instead of
// re-deriving it is the whole point: there is one answer, and this is how the electron-builder
// config gets the same one the payload has.
const distPath = path.join(payloadDir, 'distribution.toml')
if (!fs.existsSync(distPath)) fail(`\`agentd product payload\` wrote no distribution.toml in ${payloadRel}`)
const product = TOML.parse(fs.readFileSync(distPath, 'utf-8')).product || {}
const name = String(product.name || agentId)
const appId = String(product.app_id || `dev.agentd.app.${agentId}`)
const bundlesDir = path.join(payloadDir, 'bundles')
const packed = fs.existsSync(bundlesDir) ? fs.readdirSync(bundlesDir).find((f) => f.endsWith('.agentpkg')) : ''
if (!packed) fail(`no .agentpkg in ${flavorRel}/bundles`)
// <id>-<version>.agentpkg — the version is the one the packer decided, so the exe cannot claim
// a different number than the bundle inside it.
const version = (/-([0-9][^-]*)\.agentpkg$/.exec(packed) || [, '1.0.0'])[1]

// ---- electron-builder config (per-agent file > env-macro tricks: deterministic) ---------
const icon = fs.existsSync(path.join(payloadDir, 'icon.ico'))
  ? `${payloadRel}/icon.ico`
  : 'resources/icon.ico'
const eb = `# ${name} installer — generated by gen-app-flavor.mjs. Same shell code as every
# product; only these resources differ. Build: npm run dist:app -- ${agentId}
appId: ${appId}
productName: ${name}
# The product's version is the AGENT's, read back out of the payload the runtime just wrote.
# Without this override electron-builder takes it from clients/desktop/package.json, so every
# per-agent installer claimed the SHELL version no matter what the agent declared — "Figure
# Creator Setup 0.1.5.exe" for an agent whose agent.toml said 1.1.0. The version is also how the
# publisher tooling matches a delivered installer to its bundle (bundle publish -> _stage_installers),
# so a wrong one here makes every correct publish print a mismatch warning.
extraMetadata:
  version: ${version}
# npm WORKSPACES: electron-builder's implicit production install is rooted at clients/ and
# prunes its devDependencies. Nothing here is native, so skip it (matches electron-builder.yml).
npmRebuild: false
directories:
  output: dist/app/${agentId}
files:
  - out/**
extraResources:
  - from: ${payloadRel}/distribution.toml
    to: distribution.toml
  - from: ${payloadRel}/bundles
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
  include: build/engine-register.nsh # records this install in the engine registry contract
`
fs.writeFileSync(path.join(flavorDir, 'electron-builder.yml'), eb)

console.log(`\nflavor generated: ${flavorRel}/  (product: "${name}" v${version}; build output, gitignored)`)
console.log(`  dev run:   npx cross-env AGENTD_FLAVOR=${agentId} electron-vite dev`)
console.log(`  installer: npm run dist:app -- ${agentId}`)
console.log(`  or, ~200 KB sharing one engine:  agentd product build ${agentId}`)

/**
 * dist-app — build one agent's installer and DELIVER it into the agent's own folder.
 *
 *   npm run dist:app -- <agent-id>
 *
 * Requires a generated flavor (run `npm run gen:app -- <id>` first) and the embedded
 * runtime at runtime/cpython (scripts/build-runtime.ps1).
 *
 * Everything an agent owns lives in agents/<id>/ — including its built products:
 * the installer lands in agents/<id>/clients/desktop/. The factory (this project)
 * keeps only scratch: dist/app-flavors/<id>/ (config) and a transient dist/app/<id>/
 * staging dir that is emptied after delivery. The packer excludes agents/<id>/clients/
 * (bundle_io EXCLUDED_DIRS), so a built product can never end up inside the .agentpkg.
 * Third-party intake (gen:app --pkg, no local agent dir) leaves the exe in dist/app/<id>/.
 */

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(desktopDir, '..', '..', '..') // pc-agent/
const agentId = (process.argv[2] || '').trim()
if (!agentId) {
  console.error('usage: npm run dist:app -- <agent-id>')
  process.exit(1)
}
const config = path.join(desktopDir, 'dist', 'app-flavors', agentId, 'electron-builder.yml')
if (!fs.existsSync(config)) {
  console.error(`no generated flavor for '${agentId}' — run: npm run gen:app -- ${agentId}`)
  process.exit(1)
}
if (!fs.existsSync(path.join(desktopDir, 'runtime', 'cpython'))) {
  console.error('missing runtime/cpython — run scripts/build-runtime.ps1 first')
  process.exit(1)
}

const run = (cmd, args) => {
  const r = spawnSync(cmd, args, { cwd: desktopDir, stdio: 'inherit', shell: process.platform === 'win32' })
  if (r.status !== 0) process.exit(r.status ?? 1)
}
run('npx', ['electron-vite', 'build'])
run('npx', ['electron-builder', '--config', config])

// tidy the staging dir: keep ONLY the shippable artifacts (Setup exe + update blockmap);
// the ~1 GB win-unpacked dir and electron-builder's debug yml are intermediates.
const outDir = path.join(desktopDir, 'dist', 'app', agentId)
for (const junk of ['win-unpacked', 'builder-debug.yml', 'builder-effective-config.yaml']) {
  fs.rmSync(path.join(outDir, junk), { recursive: true, force: true })
}

// deliver: the product belongs to the AGENT — move it into agents/<id>/clients/desktop/.
const agentDir = path.join(repoRoot, 'v2', 'agents', agentId)
const shippable = fs.readdirSync(outDir).filter((f) => f.endsWith('.exe') || f.endsWith('.blockmap'))
if (fs.existsSync(path.join(agentDir, 'agent.toml'))) {
  const deliverDir = path.join(agentDir, 'clients', 'desktop')
  fs.mkdirSync(deliverDir, { recursive: true })
  for (const f of shippable) {
    fs.rmSync(path.join(deliverDir, f), { force: true })
    fs.renameSync(path.join(outDir, f), path.join(deliverDir, f))
  }
  fs.rmSync(outDir, { recursive: true, force: true }) // staging emptied — nothing lingers
  const exe = shippable.find((f) => f.endsWith('.exe'))
  console.log(`\ninstaller delivered: agents/${agentId}/clients/desktop/${exe || '(missing?)'}`)
} else {
  // --pkg intake: no local agent folder to deliver into — leave it in the factory tray
  const exe = shippable.find((f) => f.endsWith('.exe'))
  console.log(`\ninstaller ready: dist/app/${agentId}/${exe || '(missing?)'}`)
}

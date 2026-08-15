/**
 * check-runtime — refuse to package an installer around a STALE bundled daemon.
 *
 * `dist:core` / `dist:studio` copy runtime/cpython into the installer AS-IS; only
 * scripts/build-runtime.ps1 ever rebuilds the agentd wheel inside it. Nothing connected those
 * two facts, so `npm run dist:core` after editing daemon code happily shipped TODAY'S UI around
 * LAST WEEK'S daemon — and the result debugs like a haunted house: `npm run dev` works (live
 * code), the installed exe fails (frozen code), and every log line contradicts the source you
 * are reading.
 *
 * The check is mtime-based, not version-based, deliberately: the wheel version rarely changes
 * between edits, so "runtime version == tree version" would have passed on the exact staleness
 * this exists to stop. If any file the wheel embeds is newer than the runtime's installed copy
 * of agent_runtime, the runtime is stale — rebuild it.
 *
 * Escape hatch: AGENTD_SKIP_RUNTIME_CHECK=1 (packaging a deliberately old daemon is an odd but
 * legitimate thing for an operator to do once; silently doing it every day is not).
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const v2 = path.resolve(desktopDir, '..', '..')

if (process.env.AGENTD_SKIP_RUNTIME_CHECK === '1') {
  console.log('[check-runtime] skipped (AGENTD_SKIP_RUNTIME_CHECK=1)')
  process.exit(0)
}

// The bundled daemon's assembly time: pip stamps files at install, so any module inside the
// runtime's site-packages says when build-runtime.ps1 last ran.
const marker = path.join(
  desktopDir, 'runtime', 'cpython', 'Lib', 'site-packages', 'agent_runtime', '__init__.py'
)
if (!fs.existsSync(marker)) {
  console.error(
    '[check-runtime] no bundled daemon at runtime/cpython — the installer would fall back to ' +
      'an `agentd` on PATH, which no real user has.\n' +
      '  fix: powershell -File scripts/build-runtime.ps1'
  )
  process.exit(1)
}
const runtimeTime = fs.statSync(marker).mtimeMs

// THE BROWSER, which pip never downloads. Same class of failure as a stale daemon and the same
// answer: refuse here rather than let it surface on a user's machine, where the symptom is
// "Executable doesn't exist" from a tool they just asked an agent to use.
const browsers = path.join(desktopDir, 'runtime', 'ms-playwright')
const hasChromium =
  fs.existsSync(browsers) && fs.readdirSync(browsers).some((d) => d.startsWith('chromium'))
if (!hasChromium) {
  console.error(
    '[check-runtime] no chromium at runtime/ms-playwright — the installer would ship a runtime ' +
      'whose browser tools cannot start (verify_app, the browser tool).\n' +
      '  fix: powershell -File scripts/build-runtime.ps1'
  )
  process.exit(1)
}

// Everything the wheel embeds (scripts/hatch_build.py): the daemon source, the shared plugin
// bundles, and the packaging inputs themselves.
const WATCHED = [
  path.join(v2, 'agent_runtime'),
  path.join(v2, 'plugins'),
  path.join(v2, 'pyproject.toml'),
  path.join(v2, 'scripts', 'hatch_build.py'),
]
const SKIP_DIRS = new Set(['__pycache__', '.pytest_cache', 'node_modules', '.git'])

let newest = 0
let newestFile = ''
function scan(entry) {
  const stat = fs.statSync(entry)
  if (stat.isFile()) {
    if (stat.mtimeMs > newest) {
      newest = stat.mtimeMs
      newestFile = entry
    }
    return
  }
  for (const name of fs.readdirSync(entry)) {
    if (SKIP_DIRS.has(name)) continue
    scan(path.join(entry, name))
  }
}
for (const w of WATCHED) if (fs.existsSync(w)) scan(w)

if (newest > runtimeTime) {
  const minutes = Math.round((newest - runtimeTime) / 60000)
  console.error(
    `[check-runtime] the bundled daemon is STALE: ${path.relative(v2, newestFile)} is newer ` +
      `than runtime/cpython by ~${minutes} min. The installer would ship today's UI around an ` +
      'old daemon.\n' +
      '  fix: powershell -File scripts/build-runtime.ps1   (then re-run this dist command)\n' +
      '  ship the old daemon anyway: AGENTD_SKIP_RUNTIME_CHECK=1'
  )
  process.exit(1)
}
console.log('[check-runtime] bundled daemon is current')

// DEV-ONLY: stop any running agentd daemon before `electron-vite dev` starts, so the
// supervisor spawns a FRESH daemon that loads the current backend code.
//
// Why this exists: Python does not hot-reload edited modules, and the desktop supervisor
// (src/main/supervisor.ts) reconnects to an already-running daemon instead of restarting
// it. So without this, edits under v2/agentd/** never take effect on a plain `npm run dev`
// — you keep talking to the stale daemon that booted before your change.
//
// Mechanism mirrors agentd/lifecycle.py:stop_daemon — read the rendezvous file the daemon
// writes on bind (~/.agentd/gateway.json; AGENTD_HOME overrides), kill that pid, and clear
// the file so the supervisor treats the next start as fresh. Deliberately does NOT shell
// out to `agentd stop`: the repo venv may not be on PATH in the shell running npm, whereas
// Node is guaranteed here. This is a DEV convenience only — production must never kill the
// daemon on client start (it is a shared, user-level service; see the answer in chat).

import { spawnSync } from 'node:child_process'
import { readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const home = (process.env.AGENTD_HOME || '').trim() || path.join(os.homedir(), '.agentd')
const rendezvous = path.join(home, 'gateway.json')

let pid = 0
try {
  pid = Number(JSON.parse(readFileSync(rendezvous, 'utf-8')).pid || 0)
} catch {
  // no (readable) rendezvous file => no daemon to stop
}

if (pid > 0) {
  try {
    if (process.platform === 'win32') {
      // /T also kills the child tree — the `agentd` console-script forks a python child
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })
    } else {
      process.kill(pid, 'SIGTERM')
    }
    console.log(`[predev] stopped agentd daemon (pid ${pid}) — a fresh one will load current backend code`)
  } catch {
    // already gone
  }
}

// Clear the rendezvous unconditionally so the supervisor spawns fresh rather than adopting
// a file that points at a now-dead port.
try {
  rmSync(rendezvous, { force: true })
} catch {
  // nothing to clear
}

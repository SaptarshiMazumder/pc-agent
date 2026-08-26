// Stop EVERY running agentd — the daemon, orphaned `agentd chat` CLIs, a packaged runtime's
// python, the desktop shell. Runs as the `predev` hook and standalone, any time:
//
//   node scripts/kill-daemon.mjs
//
// Why a sweep and not the rendezvous pid: this script used to read ~/.agentd/gateway.json and
// kill the ONE pid in it. That file describes at most one daemon — it says nothing about a
// second daemon from another checkout, orphaned chat CLIs, or a daemon whose file went stale —
// so "kill agentd" routinely left agentd running, and a stale file made it "fail" by killing a
// pid that was already dead. The process list is the truth; the file is just a hint. So: find
// every process that IS agentd (by name, or `agent_runtime` on its command line — the module
// path every daemon/CLI invocation carries), kill each tree, then clear the rendezvous so the
// next supervisor start spawns fresh instead of adopting a corpse.
//
// Python does not hot-reload edited modules and the desktop supervisor reconnects to an
// already-running daemon instead of restarting it — that is why `predev` runs this: without it,
// edits under v2/agent_runtime/** never take effect on a plain `npm run dev`. A DEV/operator
// tool only — the production shell must never kill the daemon on client start (it is a shared,
// user-level service).

import { spawnSync } from 'node:child_process'
import { rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** Every live agentd process: [{pid, name, cmd}]. */
function findAgentdProcesses() {
  if (process.platform === 'win32') {
    // CIM, not tasklist: only the command line tells an agentd python from any other python.
    // $PID is the spawned powershell itself — its command line contains 'agent_runtime'
    // (this very query), as does this node process's argv, so both are excluded by pid.
    const query = [
      "Get-CimInstance Win32_Process | Where-Object {",
      `  ($_.Name -eq 'agentd.exe' -or $_.CommandLine -match 'agent_runtime')`,
      `  -and $_.ProcessId -ne $PID -and $_.ProcessId -ne ${process.pid}`,
      "} | ForEach-Object { \"$($_.ProcessId)`t$($_.Name)`t$($_.CommandLine)\" }",
    ].join(' ')
    const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', query], {
      encoding: 'utf-8',
    })
    if (r.status !== 0) {
      console.error('[kill-daemon] could not list processes:\n' + (r.stderr || r.stdout || ''))
      process.exit(1)
    }
    return (r.stdout || '')
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const [pid, name, ...cmd] = l.split('\t')
        return { pid: Number(pid), name, cmd: cmd.join('\t') }
      })
      .filter((p) => p.pid > 0)
  }
  // unix: same idea off ps. Excludes itself the same way — by pid, not by pattern.
  const r = spawnSync('ps', ['-eo', 'pid=,comm=,args='], { encoding: 'utf-8' })
  if (r.status !== 0) {
    console.error('[kill-daemon] could not list processes:\n' + (r.stderr || ''))
    process.exit(1)
  }
  return (r.stdout || '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = l.match(/^(\d+)\s+(\S+)\s+(.*)$/)
      return m ? { pid: Number(m[1]), name: m[2], cmd: m[3] } : null
    })
    .filter(
      (p) =>
        p &&
        p.pid !== process.pid &&
        (p.name === 'agentd' || p.cmd.includes('agent_runtime')),
    )
}

const procs = findAgentdProcesses()
for (const p of procs) {
  if (process.platform === 'win32') {
    // /T kills the child tree — the `agentd` console-script forks a python child. Trees overlap,
    // so a pid may be gone by its own turn; that is success, not an error.
    spawnSync('taskkill', ['/PID', String(p.pid), '/T', '/F'], { stdio: 'ignore' })
  } else {
    try {
      process.kill(p.pid, 'SIGKILL')
    } catch {
      // already gone — its parent's tree took it
    }
  }
  console.log(`[kill-daemon] killed ${p.pid} ${p.name} — ${p.cmd.slice(0, 100)}`)
}
if (procs.length === 0) console.log('[kill-daemon] no agentd processes running')

// Clear the rendezvous unconditionally so the supervisor spawns fresh rather than adopting
// a file that points at a now-dead port.
const home = (process.env.AGENTD_HOME || '').trim() || path.join(os.homedir(), '.agentd')
try {
  rmSync(path.join(home, 'gateway.json'), { force: true })
} catch {
  // nothing to clear
}

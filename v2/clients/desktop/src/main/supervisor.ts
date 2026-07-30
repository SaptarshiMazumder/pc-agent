/**
 * Daemon supervisor — the desktop shell's half of "the user never sees Python".
 *
 * Ensure-running mirror of agent_runtime/lifecycle.py: find the live daemon via the
 * rendezvous file, else spawn one DETACHED and wait for the file + an open port.
 * The daemon command resolves (first hit wins):
 *   1. AGENTD_DAEMON_CMD                      (explicit override, also what dev uses)
 *   2. <resources>/python python -m agent_runtime    (packaged: the embedded runtime — a
 *      RELOCATABLE python-build-standalone CPython, NOT a venv (venvs bake in an
 *      absolute base-interpreter path and don't survive being moved to the user's
 *      machine) and NOT a frozen exe, so marketplace pip-plugins can still install)
 *   3. `agentd` on PATH                        (a pipx/uv install on this machine)
 *   4. `python -m agent_runtime`                      (last resort, dev checkouts)
 *
 * The supervisor never stops the daemon on app quit by default: it is a USER-level
 * service (cron jobs, channels keep running) — the shell is just one client of it.
 */

import { app } from 'electron'
import { spawn } from 'node:child_process'
import { promises as fs } from 'node:fs'
import fsSync from 'node:fs'
import path from 'node:path'

import {
  agentdHome,
  clearGatewayFile,
  findRunning,
  GatewayInfo,
  portOpen,
  readGatewayFile
} from './rendezvous'

export type SupervisorPhase = 'looking' | 'starting' | 'running' | 'failed'

export interface SupervisorStatus {
  phase: SupervisorPhase
  message: string
  info: GatewayInfo | null
}

type StatusListener = (status: SupervisorStatus) => void

const SPAWN_WAIT_MS = 300_000 // cold container builds (imports) can take minutes

function commandCandidates(): string[][] {
  const override = (process.env.AGENTD_DAEMON_CMD || '').trim()
  if (override) return [override.split(/\s+/)]
  const candidates: string[][] = []
  if (app.isPackaged) {
    const embedded =
      process.platform === 'win32'
        ? path.join(process.resourcesPath, 'python', 'python.exe')
        : path.join(process.resourcesPath, 'python', 'bin', 'python')
    if (fsSync.existsSync(embedded)) candidates.push([embedded, '-m', 'agent_runtime'])
  }
  candidates.push(['agentd', 'serve'])
  candidates.push([process.platform === 'win32' ? 'python' : 'python3', '-m', 'agent_runtime'])
  return candidates
}

export class Supervisor {
  private listeners: StatusListener[] = []
  private status: SupervisorStatus = { phase: 'looking', message: 'looking for agentd…', info: null }

  // Circuit-breaker. The renderer reconnects on a backoff and calls ensure() forever;
  // without a ceiling a broken build re-spawns doomed processes on every cycle — a
  // storm that can wedge the whole machine. After maxSpawnFailures failed cycles we
  // stop spawning and fail fast. Reset when a live daemon is found or on restart().
  private consecutiveFailures = 0
  private readonly maxSpawnFailures = 3

  /** getFlavorPath: injected by the composition root (index.ts) — the flavored
   *  distribution.toml the spawned daemon must inherit ('' => open build). */
  constructor(private getFlavorPath: () => string = () => '') {}

  onStatus(listener: StatusListener): void {
    this.listeners.push(listener)
    listener(this.status)
  }

  current(): SupervisorStatus {
    return this.status
  }

  private set(phase: SupervisorPhase, message: string, info: GatewayInfo | null = null): void {
    this.status = { phase, message, info }
    for (const listener of this.listeners) listener(this.status)
  }

  /** Find or start the daemon. Resolves with the live GatewayInfo, or throws.
   *  Serialized: app-ready and the renderer both call this at startup, and a second
   *  concurrent spawn would fight the daemon's single-instance guard — so they share
   *  one in-flight attempt. */
  ensure(): Promise<GatewayInfo> {
    if (!this.ensurePromise) {
      this.ensurePromise = this.doEnsure().finally(() => {
        this.ensurePromise = null
      })
    }
    return this.ensurePromise
  }

  private ensurePromise: Promise<GatewayInfo> | null = null

  /** Restart the daemon so restart-gated config changes take effect: stop the running one
   *  (kill by the pid in the rendezvous file), clear the file, then spawn a fresh daemon —
   *  which reloads agentd.config.json from scratch. The renderer's gateway auto-reconnects. */
  async restart(): Promise<GatewayInfo> {
    this.consecutiveFailures = 0 // an explicit restart re-arms the circuit-breaker
    this.set('starting', 'restarting agentd to apply changes…')
    try {
      const info = await readGatewayFile()
      if (info?.pid) {
        try {
          process.kill(info.pid)
        } catch {
          /* already gone */
        }
      }
    } catch {
      /* no rendezvous file — nothing to stop */
    }
    await clearGatewayFile()
    // let the OS release the port before we (or the renderer) re-ensure
    await new Promise((resolve) => setTimeout(resolve, 400))
    this.ensurePromise = null // force a fresh find-or-spawn, not a cached ensure
    return this.ensure()
  }

  private async doEnsure(): Promise<GatewayInfo> {
    this.set('looking', 'looking for a running agentd…')
    const existing = await findRunning()
    if (existing) {
      this.consecutiveFailures = 0 // a live daemon means we've recovered — re-arm the breaker
      this.set('running', `agentd ${existing.version} (pid ${existing.pid})`, existing)
      return existing
    }
    if (this.consecutiveFailures >= this.maxSpawnFailures) {
      // Breaker open: STOP re-spawning. We still re-checked findRunning() above, so a
      // daemon started some other way is picked up — but we will not keep spawning
      // doomed processes on every reconnect. An explicit restart() re-arms us.
      this.set(
        'failed',
        `agentd failed to start ${this.consecutiveFailures} times — not retrying automatically. See the daemon log, then use restart to try again.`
      )
      throw new Error('agentd could not start (circuit-breaker open)')
    }
    return this.spawnDaemon()
  }

  private async spawnDaemon(): Promise<GatewayInfo> {
    const logDir = path.join(agentdHome(), 'logs')
    await fs.mkdir(logDir, { recursive: true })
    const logPath = path.join(logDir, 'daemon.log')
    const logFile = fsSync.openSync(logPath, 'a')
    // clear any stale rendezvous first, so the file that (re)appears is unambiguously
    // OUR new daemon's — the console-script wrapper (`agentd`) forks a child python
    // with a different pid, so we can't match on the spawned pid.
    await clearGatewayFile()

    const env = { ...process.env }
    // A flavored build carries its distribution.toml; the daemon it spawns must be
    // the same product (provisioning, default agent, store wiring) — pass it down.
    const flavorPath = this.getFlavorPath()
    if (flavorPath && !env.AGENTD_DISTRIBUTION) env.AGENTD_DISTRIBUTION = flavorPath

    let lastError = ''
    for (const command of commandCandidates()) {
      this.set('starting', `starting agentd (${command[0]})… first start can take a minute`)
      try {
        const child = spawn(command[0], command.slice(1), {
          detached: true,
          stdio: ['ignore', logFile, logFile],
          windowsHide: true,
          env
        })
        child.unref()
        // The daemon is detached and runs forever on success, so an 'exit' before the
        // rendezvous appears means it FAILED (e.g. couldn't bind) — surface it fast
        // with the log tail instead of waiting out the full timeout.
        const spawnFailed = new Promise<never>((_, reject) => {
          child.once('error', (e) => reject(new Error(`${command[0]}: ${e.message}`)))
          child.once('exit', (code) => {
            if (code !== null && code !== 0) {
              reject(new Error(`${command[0]} exited (code ${code}) — see ${logPath}`))
            }
          })
        })
        const info = await Promise.race([this.waitForDaemon(), spawnFailed])
        this.consecutiveFailures = 0 // it came up — re-arm the breaker
        this.set('running', `agentd ${info.version} (pid ${info.pid})`, info)
        return info
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error)
      }
    }
    this.consecutiveFailures++ // trips the breaker once it reaches maxSpawnFailures
    this.set('failed', `could not start agentd: ${lastError} — see ${logPath}`)
    throw new Error(lastError || 'could not start agentd')
  }

  /** Wait for the rendezvous file to (re)appear with an open port. We cleared it
   *  before spawning, so its reappearance is our daemon — no pid match needed
   *  (the console-script wrapper's pid differs from the python server's). */
  private async waitForDaemon(): Promise<GatewayInfo> {
    const deadline = Date.now() + SPAWN_WAIT_MS
    while (Date.now() < deadline) {
      const info = await readGatewayFile()
      if (info && (await portOpen(info.host, info.port))) {
        return info
      }
      await new Promise((resolve) => setTimeout(resolve, 400))
    }
    throw new Error(`daemon did not come up within ${SPAWN_WAIT_MS / 1000}s`)
  }
}

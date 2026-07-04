/**
 * Daemon supervisor — the desktop shell's half of "the user never sees Python".
 *
 * Ensure-running mirror of agentd/lifecycle.py: find the live daemon via the
 * rendezvous file, else spawn one DETACHED and wait for the file + an open port.
 * The daemon command resolves (first hit wins):
 *   1. AGENTD_DAEMON_CMD                      (explicit override, also what dev uses)
 *   2. <resources>/agentd-env python -m agentd (packaged: the embedded runtime — a real
 *      venv, NOT a frozen exe, so marketplace pip-plugins can install into it)
 *   3. `agentd` on PATH                        (a pipx/uv install on this machine)
 *   4. `python -m agentd`                      (last resort, dev checkouts)
 *
 * The supervisor never stops the daemon on app quit by default: it is a USER-level
 * service (cron jobs, channels keep running) — the shell is just one client of it.
 */

import { app } from 'electron'
import { spawn } from 'node:child_process'
import { promises as fs } from 'node:fs'
import fsSync from 'node:fs'
import path from 'node:path'

import { agentdHome, findRunning, GatewayInfo, portOpen, readGatewayFile } from './rendezvous'

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
        ? path.join(process.resourcesPath, 'agentd-env', 'Scripts', 'python.exe')
        : path.join(process.resourcesPath, 'agentd-env', 'bin', 'python')
    if (fsSync.existsSync(embedded)) candidates.push([embedded, '-m', 'agentd'])
  }
  candidates.push(['agentd', 'serve'])
  candidates.push([process.platform === 'win32' ? 'python' : 'python3', '-m', 'agentd'])
  return candidates
}

export class Supervisor {
  private listeners: StatusListener[] = []
  private status: SupervisorStatus = { phase: 'looking', message: 'looking for agentd…', info: null }

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

  /** Find or start the daemon. Resolves with the live GatewayInfo, or throws. */
  async ensure(): Promise<GatewayInfo> {
    this.set('looking', 'looking for a running agentd…')
    const existing = await findRunning()
    if (existing) {
      this.set('running', `agentd ${existing.version} (pid ${existing.pid})`, existing)
      return existing
    }
    return this.spawnDaemon()
  }

  private async spawnDaemon(): Promise<GatewayInfo> {
    const logDir = path.join(agentdHome(), 'logs')
    await fs.mkdir(logDir, { recursive: true })
    const logPath = path.join(logDir, 'daemon.log')
    const logFile = fsSync.openSync(logPath, 'a')

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
        const spawnFailed = new Promise<never>((_, reject) =>
          child.once('error', (e) => reject(new Error(`${command[0]}: ${e.message}`)))
        )
        const info = await Promise.race([this.waitForDaemon(child.pid ?? 0), spawnFailed])
        this.set('running', `agentd ${info.version} (pid ${info.pid})`, info)
        return info
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error)
      }
    }
    this.set('failed', `could not start agentd: ${lastError} — see ${logPath}`)
    throw new Error(lastError || 'could not start agentd')
  }

  private async waitForDaemon(childPid: number): Promise<GatewayInfo> {
    const deadline = Date.now() + SPAWN_WAIT_MS
    while (Date.now() < deadline) {
      const info = await readGatewayFile()
      if (info && (childPid === 0 || info.pid === childPid) && (await portOpen(info.host, info.port))) {
        return info
      }
      await new Promise((resolve) => setTimeout(resolve, 400))
    }
    throw new Error(`daemon did not come up within ${SPAWN_WAIT_MS / 1000}s`)
  }
}

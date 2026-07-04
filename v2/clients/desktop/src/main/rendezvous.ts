/**
 * Daemon rendezvous — the TypeScript mirror of agentd/lifecycle.py (read side).
 *
 * One file, ~/.agentd/gateway.json (AGENTD_HOME overrides), written by the daemon
 * when it binds: {host, port, pid, token, version, started_at}. The token rides the
 * connect URL as a query param — the only slot browser WebSockets have.
 */

import { promises as fs } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

export interface GatewayInfo {
  host: string
  port: number
  pid: number
  token: string
  version: string
  startedAt: string
}

export function agentdHome(): string {
  const override = (process.env.AGENTD_HOME || '').trim()
  return override || path.join(os.homedir(), '.agentd')
}

export function gatewayFilePath(): string {
  return path.join(agentdHome(), 'gateway.json')
}

export function connectUrl(info: GatewayInfo): string {
  const base = `ws://${info.host}:${info.port}`
  return info.token ? `${base}/?token=${info.token}` : base
}

/** Remove the rendezvous file (best-effort). Mirrors lifecycle.clear_gateway_file:
 *  the supervisor clears a stale file before spawning so the daemon that (re)writes
 *  it is unambiguously the one we just started — no pid guessing across the
 *  console-script wrapper (which forks a child python with a different pid). */
export async function clearGatewayFile(): Promise<void> {
  try {
    await fs.rm(gatewayFilePath(), { force: true })
  } catch {
    /* nothing to clear */
  }
}

export async function readGatewayFile(): Promise<GatewayInfo | null> {
  try {
    const raw = await fs.readFile(gatewayFilePath(), 'utf-8')
    const data = JSON.parse(raw)
    if (!data.port) return null
    return {
      host: String(data.host || '127.0.0.1'),
      port: Number(data.port),
      pid: Number(data.pid || 0),
      token: String(data.token || ''),
      version: String(data.version || ''),
      startedAt: String(data.started_at || '')
    }
  } catch {
    return null
  }
}

export function portOpen(host: string, port: number, timeoutMs = 1000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port })
    const done = (ok: boolean) => {
      socket.destroy()
      resolve(ok)
    }
    socket.setTimeout(timeoutMs, () => done(false))
    socket.once('connect', () => done(true))
    socket.once('error', () => done(false))
  })
}

/** The live daemon per the rendezvous file, or null (stale files are just ignored —
 *  the python side owns cleanup). */
export async function findRunning(): Promise<GatewayInfo | null> {
  const info = await readGatewayFile()
  if (info === null) return null
  return (await portOpen(info.host, info.port)) ? info : null
}

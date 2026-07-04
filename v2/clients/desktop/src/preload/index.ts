/**
 * Preload — the ONLY bridge between the sandboxed renderer and the main process.
 * Exposes a minimal, typed surface (window.agentd): flavor, supervisor, gateway URL.
 * The renderer talks to the daemon DIRECTLY over WebSocket; nothing agent-related
 * flows through IPC.
 */

import { contextBridge, ipcRenderer } from 'electron'

const api = {
  flavor: () => ipcRenderer.invoke('app:flavor'),
  supervisorStatus: () => ipcRenderer.invoke('supervisor:status'),
  /** find-or-start the daemon; resolves {url, version, pid} when it's accepting */
  ensureDaemon: () => ipcRenderer.invoke('supervisor:ensure'),
  onSupervisorStatus: (callback: (status: unknown) => void) => {
    const listener = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on('supervisor:status-changed', listener)
    return () => ipcRenderer.removeListener('supervisor:status-changed', listener)
  }
}

contextBridge.exposeInMainWorld('agentd', api)

export type DesktopApi = typeof api

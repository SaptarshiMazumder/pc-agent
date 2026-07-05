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
  },
  // local files: open an agent artifact in the OS default app / reveal it in the file
  // manager, and pick attachments (returns each file's name + base64 bytes to upload)
  openPath: (p: string): Promise<string> => ipcRenderer.invoke('file:open', p),
  revealPath: (p: string): Promise<void> => ipcRenderer.invoke('file:reveal', p),
  pickFiles: (): Promise<Array<{ name: string; size: number; dataBase64: string }>> =>
    ipcRenderer.invoke('file:pick')
}

contextBridge.exposeInMainWorld('agentd', api)

export type DesktopApi = typeof api

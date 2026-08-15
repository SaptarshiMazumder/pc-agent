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
  // OS-encrypted storage for the refresh token (see src/main/index.ts). Shaped as the renderer's
  // RefreshStorage interface so lib/tokens.ts can use it without knowing it is Electron.
  secrets: {
    read: (): Promise<string | null> => ipcRenderer.invoke('secrets:read'),
    write: (token: string | null): Promise<void> => ipcRenderer.invoke('secrets:write', token)
  },
  /** find-or-start the daemon; resolves {url, version, pid} when it's accepting */
  ensureDaemon: () => ipcRenderer.invoke('supervisor:ensure'),
  /** stop + respawn the daemon so restart-gated config changes take effect */
  restartDaemon: () => ipcRenderer.invoke('supervisor:restart'),
  onSupervisorStatus: (callback: (status: unknown) => void) => {
    const listener = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on('supervisor:status-changed', listener)
    return () => ipcRenderer.removeListener('supervisor:status-changed', listener)
  },
  // local files: open an agent artifact in the OS default app / reveal it in the file
  // manager, and pick attachments (returns each file's name + base64 bytes to upload)
  openPath: (p: string): Promise<string> => ipcRenderer.invoke('file:open', p),
  revealPath: (p: string): Promise<void> => ipcRenderer.invoke('file:reveal', p),
  downloadPath: (p: string): Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }> =>
    ipcRenderer.invoke('file:download', p),
  readText: (p: string): Promise<{ ok: boolean; text?: string; error?: string }> =>
    ipcRenderer.invoke('file:read', p),
  readBytes: (p: string): Promise<{ ok: boolean; base64?: string; error?: string }> =>
    ipcRenderer.invoke('file:readBytes', p),
  savePath: (p: string, content: string, base64?: boolean): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('file:save', p, content, base64),
  saveAs: (defaultName: string, content: string, base64?: boolean): Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }> =>
    ipcRenderer.invoke('file:saveAs', defaultName, content, base64),
  pickFiles: (): Promise<Array<{ name: string; size: number; dataBase64: string }>> =>
    ipcRenderer.invoke('file:pick'),
  /** open an AGENT APP's daemon-served UI (/apps/<id>/) in its own desktop window */
  broadcastAppToken: (token: string): Promise<unknown> =>
    ipcRenderer.invoke('app:broadcastToken', token),
  openAppWindow: (url: string, title?: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('app:openWindow', url, title)
}

contextBridge.exposeInMainWorld('agentd', api)

export type DesktopApi = typeof api
